import json
from pathlib import Path
from typing import Any, Dict, Optional
from fastapi import FastAPI, Request
from pydantic import BaseModel

from .device import AndroidDeviceController
from .model import TinyToolCaller, DEFAULT_ADAPTER_DIR

app = FastAPI(title="Neddle - Tiny Mobile Agent & Claude Bridge")

TOOLS_PATH = Path("./data/tools.json")
caller: Optional[TinyToolCaller] = None
controller = AndroidDeviceController(dry_run_if_disconnected=True)


def get_caller() -> TinyToolCaller:
    global caller
    if caller is None:
        caller = TinyToolCaller(adapter_dir=DEFAULT_ADAPTER_DIR)
    return caller


def load_tools() -> list[Dict[str, Any]]:
    return json.loads(TOOLS_PATH.read_text(encoding="utf-8"))


class NaturalLanguageActionRequest(BaseModel):
    request: str
    execute_on_device: bool = True


@app.post("/claude-action")
async def claude_action(payload: NaturalLanguageActionRequest):
    """Receive a natural language instruction from Claude, route via the local model, and execute on Android."""
    tools = load_tools()
    predicted_call = get_caller().predict(payload.request, tools)
    device_result = None
    if payload.execute_on_device and predicted_call.get("name") != "error_unparsed":
        device_result = controller.execute_tool_call(predicted_call)
    return {
        "user_request": payload.request,
        "predicted_tool_call": predicted_call,
        "device_execution": device_result,
    }


@app.post("/mcp")
async def mcp_jsonrpc_handler(request: Request):
    """Minimal Model Context Protocol (MCP) JSON-RPC 2.0 endpoint for Claude custom connectors."""
    body = await request.json()
    method = body.get("method")
    req_id = body.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "neddle-mobile-agent-bridge", "version": "1.0.0"},
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "mobile_ai_helper",
                        "description": (
                            "Send a natural language instruction to the on-device AI helper to "
                            "install an APK, launch an Android app, inspect active UI elements, "
                            "capture a screenshot, or check logcat to verify the app is working fine."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "instruction": {
                                    "type": "string",
                                    "description": "Natural language command, e.g., 'Install ./app-debug.apk and check if it works'",
                                }
                            },
                            "required": ["instruction"],
                        },
                    }
                ]
            },
        }

    if method == "tools/call":
        params = body.get("params", {})
        args = params.get("arguments", {})
        instruction = args.get("instruction", "")
        tools = load_tools()
        predicted_call = get_caller().predict(instruction, tools)
        execution_report = controller.execute_tool_call(predicted_call)
        summary = json.dumps(
            {"predicted_tool_call": predicted_call, "device_verification": execution_report},
            indent=2,
        )
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"content": [{"type": "text", "text": summary}]},
        }

    return {"jsonrpc": "2.0", "id": req_id, "result": {}}
