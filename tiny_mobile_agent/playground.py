import json
from pathlib import Path
from typing import Any, Dict, List
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .device import AndroidDeviceController
from .model import TinyToolCaller, DEFAULT_ADAPTER_DIR

playground_app = FastAPI(title="Neddle Tiny Mobile Agent Playground")
TOOLS_PATH = Path("./data/tools.json")
SEED_PATH = Path("./data/seed_examples.jsonl")

caller: TinyToolCaller | None = None
controller = AndroidDeviceController(dry_run_if_disconnected=True)


def get_caller() -> TinyToolCaller:
    global caller
    if caller is None:
        caller = TinyToolCaller(adapter_dir=DEFAULT_ADAPTER_DIR)
    return caller


class PlaygroundPredictRequest(BaseModel):
    query: str
    tools: List[Dict[str, Any]]
    execute_device: bool = True


class PlaygroundFinetuneRequest(BaseModel):
    examples: List[Dict[str, Any]]
    tools: List[Dict[str, Any]]
    epochs: int = 3


@playground_app.post("/api/predict")
async def api_predict(req: PlaygroundPredictRequest):
    pred = get_caller().predict(req.query, req.tools)
    device_res = controller.execute_tool_call(pred) if req.execute_device else None
    return {"predicted_tool_call": pred, "device_result": device_res}


@playground_app.post("/api/finetune")
async def api_finetune(req: PlaygroundFinetuneRequest):
    stats = get_caller().finetune(req.examples, req.tools, epochs=req.epochs)
    return stats


@playground_app.get("/", response_class=HTMLResponse)
async def index():
    tools_json = TOOLS_PATH.read_text(encoding="utf-8") if TOOLS_PATH.exists() else "[]"
    seed_lines = SEED_PATH.read_text(encoding="utf-8").strip().splitlines() if SEED_PATH.exists() else []
    seed_json = json.dumps([json.loads(line) for line in seed_lines], indent=2)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Neddle Mobile Agent Playground</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 920px; margin: 0 auto; padding: 16px; background: #0f172a; color: #f8fafc; }}
    h1 {{ font-size: 1.4rem; margin-bottom: 4px; }}
    .card {{ background: #1e293b; border-radius: 10px; padding: 16px; margin-bottom: 16px; border: 1px solid #334155; }}
    label {{ display: block; font-weight: 600; margin-bottom: 6px; font-size: 0.9rem; color: #93c5fd; }}
    textarea, input[type="text"] {{ width: 100%; box-sizing: border-box; background: #0f172a; color: #e2e8f0; border: 1px solid #475569; border-radius: 6px; padding: 10px; font-family: monospace; font-size: 0.85rem; margin-bottom: 12px; }}
    button {{ background: #3b82f6; color: white; border: none; padding: 10px 16px; border-radius: 6px; font-weight: 600; cursor: pointer; }}
    button.secondary {{ background: #10b981; }}
    pre {{ background: #020617; padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 0.85rem; color: #a7f3d0; }}
  </style>
</head>
<body>
  <h1>Neddle Tiny Mobile Agent Playground</h1>
  <p style="color:#94a3b8; font-size:0.9rem;">Test natural language mobile QA requests, inspect predicted JSON tool calls, verify on Android, and finetune locally.</p>

  <div class="card">
    <label>User / Claude Request</label>
    <input type="text" id="query" value="Install ./builds/app-debug.apk overwriting the old version" />
    <label>Available Tool Descriptions (JSON)</label>
    <textarea id="tools" rows="10">{tools_json}</textarea>
    <button onclick="runPredict()">Predict & Execute Tool Call</button>
    <pre id="output">Click "Predict & Execute Tool Call" to run the local model...</pre>
  </div>

  <div class="card">
    <label>Finetune on Custom Examples (JSON Array)</label>
    <textarea id="examples" rows="10">{seed_json}</textarea>
    <button class="secondary" onclick="runFinetune()">Finetune Local Model (LoRA)</button>
    <pre id="ft_output">Ready to finetune.</pre>
  </div>

  <script>
    async function runPredict() {{
      const out = document.getElementById('output');
      out.textContent = 'Running local model inference...';
      const res = await fetch('/api/predict', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{
          query: document.getElementById('query').value,
          tools: JSON.parse(document.getElementById('tools').value),
          execute_device: true
        }})
      }});
      out.textContent = JSON.stringify(await res.json(), null, 2);
    }}

    async function runFinetune() {{
      const out = document.getElementById('ft_output');
      out.textContent = 'Finetuning LoRA adapter on local examples...';
      const res = await fetch('/api/finetune', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{
          examples: JSON.parse(document.getElementById('examples').value),
          tools: JSON.parse(document.getElementById('tools').value),
          epochs: 3
        }})
      }});
      out.textContent = JSON.stringify(await res.json(), null, 2);
    }}
  </script>
</body>
</html>"""
