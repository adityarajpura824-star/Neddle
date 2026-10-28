import json
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import torch
from datasets import Dataset
from peft import LoraConfig, PeftModel, get_peft_model
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    DataCollatorForLanguageModeling,
    Trainer,
    TrainingArguments,
)

DEFAULT_MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"
DEFAULT_ADAPTER_DIR = "./checkpoints/mobile-tool-lora"

SYSTEM_PROMPT = (
    "You are a lightweight on-device tool router. "
    "Given the available tools and a user request, respond ONLY with a valid JSON object "
    "containing 'name' and 'arguments'."
)


class TinyToolCaller:
    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        adapter_dir: Optional[str] = None,
        device: Optional[str] = None,
    ):
        self.model_id = model_id
        self.device = device or (
            "cuda" if torch.cuda.is_available()
            else "mps" if torch.backends.mps.is_available()
            else "cpu"
        )
        print(f"[TinyToolCaller] Loading {model_id} on {self.device} (downloads automatically on first run)...")
        self.tokenizer = AutoTokenizer.from_pretrained(model_id)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        self.model = AutoModelForCausalLM.from_pretrained(
            model_id,
            torch_dtype=torch.float16 if self.device != "cpu" else torch.float32,
        ).to(self.device)

        if adapter_dir and Path(adapter_dir).exists():
            print(f"[TinyToolCaller] Loading finetuned LoRA adapter from {adapter_dir}...")
            self.model = PeftModel.from_pretrained(self.model, adapter_dir).to(self.device)

        self.model.eval()

    @staticmethod
    def format_prompt(query: str, tools: List[Dict[str, Any]], target_json: Optional[Dict[str, Any]] = None) -> str:
        tools_compact = json.dumps(tools, separators=(",", ":"))
        prompt = (
            f"<|im_start|>system\n{SYSTEM_PROMPT}\n"
            f"Available Tools:\n{tools_compact}<|im_end|>\n"
            f"<|im_start|>user\n{query}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
        if target_json is not None:
            prompt += f"{json.dumps(target_json)}<|im_end|>"
        return prompt

    def predict(self, query: str, tools: List[Dict[str, Any]], max_new_tokens: int = 128) -> Dict[str, Any]:
        """Run a natural language query and return a structured JSON tool call."""
        prompt = self.format_prompt(query, tools)
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.device)

        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=self.tokenizer.pad_token_id,
            )

        generated_ids = outputs[0][inputs["input_ids"].shape[1]:]
        raw_text = self.tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
        return self._extract_json(raw_text)

    @staticmethod
    def _extract_json(text: str) -> Dict[str, Any]:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if "name" in parsed and "arguments" in parsed:
                    return parsed
            except json.JSONDecodeError:
                pass
        return {"name": "error_unparsed", "arguments": {}, "raw_output": text}

    def tokenize_preview(self, query: str, tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Inspect token count and token strings for a prompt."""
        prompt = self.format_prompt(query, tools)
        token_ids = self.tokenizer.encode(prompt)
        tokens = [self.tokenizer.decode([tid]) for tid in token_ids]
        return {
            "token_count": len(token_ids),
            "token_ids": token_ids[:40],
            "token_preview": tokens[:40],
            "formatted_prompt": prompt,
        }

    def finetune(
        self,
        examples: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        output_dir: str = DEFAULT_ADAPTER_DIR,
        epochs: int = 4,
        lr: float = 2e-4,
    ) -> Dict[str, Any]:
        """Finetune the tiny model with LoRA on user-supplied or seed examples."""
        texts = [
            self.format_prompt(ex["query"], tools, ex["tool_call"])
            for ex in examples
        ]
        dataset = Dataset.from_dict({"text": texts})

        def tokenize_fn(batch):
            return self.tokenizer(
                batch["text"],
                truncation=True,
                max_length=512,
                padding="max_length",
            )

        tokenized_ds = dataset.map(tokenize_fn, batched=True, remove_columns=["text"])

        lora_config = LoraConfig(
            r=8,
            lora_alpha=16,
            target_modules=["q_proj", "v_proj", "k_proj", "o_proj"],
            lora_dropout=0.05,
            bias="none",
            task_type="CAUSAL_LM",
        )
        peft_model = get_peft_model(self.model, lora_config)
        peft_model.train()

        training_args = TrainingArguments(
            output_dir=output_dir,
            num_train_epochs=epochs,
            per_device_train_batch_size=2,
            learning_rate=lr,
            logging_steps=1,
            save_strategy="no",
            report_to="none",
            fp16=(self.device == "cuda"),
        )

        trainer = Trainer(
            model=peft_model,
            args=training_args,
            train_dataset=tokenized_ds,
            data_collator=DataCollatorForLanguageModeling(self.tokenizer, mlm=False),
        )
        train_result = trainer.train()
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        peft_model.save_pretrained(output_dir)
        self.model = peft_model.eval()

        return {
            "status": "completed",
            "adapter_saved_to": output_dir,
            "samples_trained": len(examples),
            "epochs": epochs,
            "final_loss": round(train_result.training_loss, 4),
        }

    def evaluate(self, examples: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Evaluate tool name accuracy and exact argument match on a dataset."""
        name_correct = 0
        exact_correct = 0
        details = []

        for ex in examples:
            pred = self.predict(ex["query"], tools)
            expected = ex["tool_call"]
            is_name_match = pred.get("name") == expected.get("name")
            is_exact_match = is_name_match and (pred.get("arguments") == expected.get("arguments"))
            if is_name_match:
                name_correct += 1
            if is_exact_match:
                exact_correct += 1
            details.append({
                "query": ex["query"],
                "expected": expected,
                "predicted": pred,
                "name_match": is_name_match,
                "exact_match": is_exact_match,
            })

        total = max(len(examples), 1)
        return {
            "total_examples": len(examples),
            "tool_name_accuracy": round(name_correct / total, 4),
            "exact_call_accuracy": round(exact_correct / total, 4),
            "results": details,
        }


def generate_synthetic_data(output_path: str, count_per_tool: int = 15) -> int:
    """Generate diverse paraphrased training examples so the model generalizes rather than memorizes."""
    apk_paths = ["./app-debug.apk", "/sdcard/Download/build-42.apk", "./dist/expense-tracker-v1.2.apk", "/tmp/test.apk"]
    packages = ["com.example.expensetracker", "com.acme.todo", "org.localsync.client", "io.dev.stagingapp"]
    ui_labels = ["Add Expense", "Login", "Dashboard", "Total Balance", "Settings", "Submit"]
    cities = ["Mumbai", "Tokyo", "Berlin", "San Francisco", "London"]
    rooms = ["living room", "kitchen", "office", "bedroom", "studio"]

    templates = {
        "install_apk": [
            ("Install {apk} on my phone and overwrite if it exists", True),
            ("Can you sideload {apk} onto the device right now?", True),
            ("Do a clean install of the APK at {apk} without reinstalling", False),
            ("Deploy {apk} to the connected Android phone", True),
            ("Load {apk} onto mobile so we can test the build", True),
        ],
        "launch_app": [
            ("Open {pkg} on my phone", False),
            ("Launch {pkg} with a fresh state and wiped data", True),
            ("Start the app {pkg} right now", False),
            ("Boot up {pkg} after clearing its cache and state", True),
            ("Fire up {pkg} so I can see if it loads", False),
        ],
        "inspect_ui_state": [
            ("Verify if the text '{label}' is visible on the phone screen", True),
            ("Check the UI hierarchy to see if '{label}' rendered properly", True),
            ("Dump the current screen layout and UI elements", False),
            ("Is '{label}' showing up in the active view?", True),
            ("Inspect what buttons and views are currently on screen", False),
        ],
        "check_app_logs": [
            ("Check the last {lines} logcat lines for {pkg} to see if it crashed", None),
            ("Did {pkg} throw any fatal exceptions in the last {lines} lines?", None),
            ("Inspect crash logs for {pkg} (grab {lines} lines)", None),
            ("Pull {lines} lines of logcat for {pkg} and check health", None),
            ("See if {pkg} is working fine or crashing in logcat ({lines} lines)", None),
        ],
        "capture_screenshot": [
            ("Take a screenshot and save it to {path}", None),
            ("Grab a screen capture of the mobile app at {path}", None),
            ("Save a snapshot of the current phone screen to {path}", None),
        ],
        "get_weather": [
            ("What's the weather in {city} in {unit}?", None),
            ("Check the temperature for {city} using {unit}", None),
        ],
        "set_smart_light": [
            ("Set the {room} lights to {brightness}%", None),
            ("Turn {state} the lights in the {room}", None),
        ],
    }

    generated = []
    for _ in range(count_per_tool):
        apk = random.choice(apk_paths)
        tpl, reinstall = random.choice(templates["install_apk"])
        generated.append({
            "query": tpl.format(apk=apk),
            "tool_call": {"name": "install_apk", "arguments": {"apk_path": apk, "reinstall": reinstall}},
        })

        pkg = random.choice(packages)
        tpl, clear_state = random.choice(templates["launch_app"])
        generated.append({
            "query": tpl.format(pkg=pkg),
            "tool_call": {"name": "launch_app", "arguments": {"package_name": pkg, "clear_state": clear_state}},
        })

        label = random.choice(ui_labels)
        tpl, has_label = random.choice(templates["inspect_ui_state"])
        args = {"expected_text": label} if has_label else {}
        generated.append({
            "query": tpl.format(label=label),
            "tool_call": {"name": "inspect_ui_state", "arguments": args},
        })

        pkg = random.choice(packages)
        lines = random.choice([50, 100, 200, 300])
        tpl, _ = random.choice(templates["check_app_logs"])
        generated.append({
            "query": tpl.format(pkg=pkg, lines=lines),
            "tool_call": {"name": "check_app_logs", "arguments": {"package_name": pkg, "lines": lines}},
        })

        shot_path = f"./artifacts/screen_{random.randint(1, 999)}.png"
        tpl, _ = random.choice(templates["capture_screenshot"])
        generated.append({
            "query": tpl.format(path=shot_path),
            "tool_call": {"name": "capture_screenshot", "arguments": {"save_path": shot_path}},
        })

        city = random.choice(cities)
        unit = random.choice(["celsius", "fahrenheit"])
        tpl, _ = random.choice(templates["get_weather"])
        generated.append({
            "query": tpl.format(city=city, unit=unit),
            "tool_call": {"name": "get_weather", "arguments": {"city": city, "unit": unit}},
        })

        room = random.choice(rooms)
        brightness = random.choice([20, 50, 75, 100])
        tpl, _ = random.choice(templates["set_smart_light"])
        generated.append({
            "query": tpl.format(room=room, state="on", brightness=brightness),
            "tool_call": {"name": "set_smart_light", "arguments": {"room": room, "state": "on", "brightness": brightness}},
        })

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for row in generated:
            f.write(json.dumps(row) + "\n")

    return len(generated)
