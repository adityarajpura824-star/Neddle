import argparse
import json
from pathlib import Path
import uvicorn

from tiny_mobile_agent.device import AndroidDeviceController
from tiny_mobile_agent.model import (
    DEFAULT_ADAPTER_DIR,
    TinyToolCaller,
    generate_synthetic_data,
)


def load_json(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_jsonl(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    parser = argparse.ArgumentParser(description="Neddle Tiny Mobile Agent CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    # 1. playground
    p_play = sub.add_parser("playground", help="Launch the interactive web/mobile playground")
    p_play.add_argument("--host", default="0.0.0.0")
    p_play.add_argument("--port", type=int, default=8000)

    # 2. bridge
    p_bridge = sub.add_parser("bridge", help="Start the Claude Mobile App MCP/HTTP bridge server")
    p_bridge.add_argument("--host", default="0.0.0.0")
    p_bridge.add_argument("--port", type=int, default=8080)

    # 3. run
    p_run = sub.add_parser("run", help="Run a single natural language query and return the JSON tool call")
    p_run.add_argument("query", type=str, help="User instruction")
    p_run.add_argument("--tools", default="./data/tools.json")
    p_run.add_argument("--execute", action="store_true", help="Also execute the matched tool on the connected Android device")

    # 4. finetune
    p_ft = sub.add_parser("finetune", help="Finetune the model on custom JSONL examples using LoRA")
    p_ft.add_argument("--data", default="./data/seed_examples.jsonl")
    p_ft.add_argument("--tools", default="./data/tools.json")
    p_ft.add_argument("--epochs", type=int, default=4)
    p_ft.add_argument("--out", default=DEFAULT_ADAPTER_DIR)

    # 5. train
    p_train = sub.add_parser("train", help="Generate synthetic data if missing and run a full training cycle")
    p_train.add_argument("--data", default="./data/synthetic_train.jsonl")
    p_train.add_argument("--tools", default="./data/tools.json")
    p_train.add_argument("--epochs", type=int, default=5)

    # 6. evaluate
    p_eval = sub.add_parser("evaluate", help="Evaluate tool name and exact argument accuracy on a dataset")
    p_eval.add_argument("--data", default="./data/seed_examples.jsonl")
    p_eval.add_argument("--tools", default="./data/tools.json")

    # 7. tokenize
    p_tok = sub.add_parser("tokenize", help="Inspect prompt formatting and token counts for a request")
    p_tok.add_argument("query", type=str)
    p_tok.add_argument("--tools", default="./data/tools.json")

    # 8. generate-data
    p_gen = sub.add_parser("generate-data", help="Generate synthetic training examples per tool")
    p_gen.add_argument("--out", default="./data/synthetic_train.jsonl")
    p_gen.add_argument("--count-per-tool", type=int, default=20)

    args = parser.parse_args()

    if args.command == "playground":
        print(f"Opening Playground at http://{args.host}:{args.port}")
        uvicorn.run("tiny_mobile_agent.playground:playground_app", host=args.host, port=args.port)

    elif args.command == "bridge":
        print(f"Starting Claude Mobile MCP/Webhook Bridge at http://{args.host}:{args.port}/mcp")
        uvicorn.run("tiny_mobile_agent.bridge:app", host=args.host, port=args.port)

    elif args.command == "run":
        tools = load_json(args.tools)
        caller = TinyToolCaller(adapter_dir=DEFAULT_ADAPTER_DIR)
        prediction = caller.predict(args.query, tools)
        output = {"predicted_tool_call": prediction}
        if args.execute:
            controller = AndroidDeviceController()
            output["device_execution"] = controller.execute_tool_call(prediction)
        print(json.dumps(output, indent=2))

    elif args.command == "finetune":
        tools = load_json(args.tools)
        examples = load_jsonl(args.data)
        caller = TinyToolCaller(adapter_dir=None)
        res = caller.finetune(examples, tools, output_dir=args.out, epochs=args.epochs)
        print(json.dumps(res, indent=2))

    elif args.command == "train":
        if not Path(args.data).exists():
            total = generate_synthetic_data(args.data, count_per_tool=20)
            print(f"Generated {total} synthetic training samples at {args.data}")
        tools = load_json(args.tools)
        examples = load_jsonl(args.data)
        caller = TinyToolCaller(adapter_dir=None)
        res = caller.finetune(examples, tools, output_dir=DEFAULT_ADAPTER_DIR, epochs=args.epochs)
        print(json.dumps(res, indent=2))

    elif args.command == "evaluate":
        tools = load_json(args.tools)
        examples = load_jsonl(args.data)
        caller = TinyToolCaller(adapter_dir=DEFAULT_ADAPTER_DIR)
        report = caller.evaluate(examples, tools)
        print(json.dumps(report, indent=2))

    elif args.command == "tokenize":
        tools = load_json(args.tools)
        caller = TinyToolCaller(adapter_dir=DEFAULT_ADAPTER_DIR)
        info = caller.tokenize_preview(args.query, tools)
        print(json.dumps(info, indent=2))

    elif args.command == "generate-data":
        total = generate_synthetic_data(args.out, count_per_tool=args.count_per_tool)
        print(json.dumps({"status": "generated", "output_file": args.out, "total_examples": total}, indent=2))


if __name__ == "__main__":
    main()
