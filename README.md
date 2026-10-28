# Neddle — Tiny Mobile Tool-Calling Agent & Claude Mobile Bridge

Lightweight local function-calling model (`Qwen/Qwen2.5-0.5B-Instruct`) + Android companion app + MCP/Webhook bridge for the **Claude mobile app**.

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 1. Run a single tool-call prediction & execute on device
python cli.py run "Install ./builds/app-debug.apk over the existing version" --execute

# 2. Generate 20 synthetic examples per tool so the model generalizes
python cli.py generate-data --out ./data/synthetic_train.jsonl --count-per-tool 20

# 3. Finetune LoRA adapter locally
python cli.py finetune --data ./data/synthetic_train.jsonl --epochs 4

# 4. Evaluate accuracy
python cli.py evaluate --data ./data/seed_examples.jsonl

# 5. Inspect tokenization
python cli.py tokenize "Check logcat for com.example.expensetracker to see if it crashed"

# 6. Launch interactive Web/Mobile Playground
python cli.py playground --port 8000

# 7. Start Claude Mobile App MCP Bridge
python cli.py bridge --port 8080
```
