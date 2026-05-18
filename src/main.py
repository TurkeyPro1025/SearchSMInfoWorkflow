import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import uuid
from typing import Any, AsyncGenerator, Dict

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def ensure_project_python() -> None:
    if os.environ.get("VIBE_SKIP_REEXEC") == "1":
        return

    src_dir = os.path.dirname(__file__)
    project_root = os.path.dirname(src_dir)
    venv_python = os.path.join(project_root, ".venv", "Scripts", "python.exe")

    if not os.path.exists(venv_python):
        return

    current_python = os.path.abspath(sys.executable)
    target_python = os.path.abspath(venv_python)
    if current_python.lower() == target_python.lower():
        return

    env = os.environ.copy()
    env["VIBE_SKIP_REEXEC"] = "1"
    completed = subprocess.run([target_python, *sys.argv], env=env)
    raise SystemExit(completed.returncode)


def load_workflow_graph():
    src_dir = os.path.dirname(__file__)
    project_root = os.path.dirname(src_dir)

    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    try:
        from graphs.graph import main_graph
    except ModuleNotFoundError as exc:
        if exc.name != "graphs":
            raise
        from src.graphs.graph import main_graph

    return main_graph


class GraphService:
    def __init__(self) -> None:
        self.graph = None

    def _get_graph(self):
        if self.graph is None:
            self.graph = load_workflow_graph()
        return self.graph

    async def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        run_id = str(uuid.uuid4())
        logger.info("Starting workflow run: run_id=%s", run_id)
        graph = self._get_graph()
        result = await graph.ainvoke(
            payload,
            config={"configurable": {"thread_id": run_id}},
        )
        if isinstance(result, dict):
            result.setdefault("run_id", run_id)
        return result

    async def stream_sse(self, payload: Dict[str, Any]) -> AsyncGenerator[str, None]:
        run_id = str(uuid.uuid4())
        logger.info("Starting workflow stream: run_id=%s", run_id)
        graph = self._get_graph()
        try:
            async for output in graph.astream(
                payload,
                config={"configurable": {"thread_id": run_id}},
            ):
                yield f"data: {json.dumps(output, ensure_ascii=False, default=str)}\n\n"
        except Exception as exc:
            logger.exception("Stream run failed: run_id=%s", run_id)
            error = {"error": str(exc), "run_id": run_id}
            yield f"data: {json.dumps(error, ensure_ascii=False)}\n\n"

    async def run_node(self, node_id: str, payload: Dict[str, Any]) -> Any:
        raise NotImplementedError(f"单节点运行暂未实现: {node_id}")


service = GraphService()


def create_app():
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import StreamingResponse

    app = FastAPI()

    @app.get("/health")
    async def health_check() -> Dict[str, str]:
        return {"status": "ok", "message": "Service is running"}

    @app.post("/run")
    async def http_run(request: Request) -> Dict[str, Any]:
        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON format") from exc

        try:
            return await service.run(payload)
        except Exception as exc:
            logger.exception("/run failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/stream_run")
    async def http_stream_run(request: Request) -> StreamingResponse:
        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON format") from exc

        return StreamingResponse(service.stream_sse(payload), media_type="text/event-stream")

    @app.post("/node_run/{node_id}")
    async def http_node_run(node_id: str, request: Request) -> Any:
        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON format") from exc

        try:
            return await service.run_node(node_id, payload)
        except NotImplementedError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("/node_run failed: %s", node_id)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return app

app = None


def parse_input(input_str: str) -> Dict[str, Any]:
    if not input_str:
        payload: Dict[str, Any] = {}
    else:
        raw_input = input_str.strip()
        candidates = [raw_input]
        if len(raw_input) >= 2 and raw_input[0] == raw_input[-1] and raw_input[0] in {"'", '"'}:
            candidates.insert(0, raw_input[1:-1])

        payload = None
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue

            while isinstance(parsed, str):
                try:
                    parsed = json.loads(parsed)
                except json.JSONDecodeError:
                    break

            if isinstance(parsed, dict):
                payload = parsed
                break

        if payload is None:
            payload = {"text": input_str}

    if "app_token" not in payload:
        env_app_token = os.getenv("FEISHU_APP_TOKEN")
        if env_app_token:
            payload["app_token"] = env_app_token

    if "table_id" not in payload:
        env_table_id = os.getenv("FEISHU_TABLE_ID")
        if env_table_id:
            payload["table_id"] = env_table_id

    return payload

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="股市资讯工作流 - 本地执行入口")
    parser.add_argument("-m", type=str, default="http", help="运行模式: http / flow / node")
    parser.add_argument("-n", type=str, default="", help="节点 ID（node 模式）")
    parser.add_argument("-p", type=int, default=5000, help="HTTP 服务端口")
    parser.add_argument("-i", type=str, default="", help="输入 JSON 字符串")
    return parser.parse_args()


def start_http_server(port: int) -> None:
    import uvicorn

    logger.info("Starting HTTP server on port %s", port)
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    ensure_project_python()
    args = parse_args()

    if args.m == "http":
        start_http_server(args.p)
    elif args.m == "flow":
        result = asyncio.run(service.run(parse_input(args.i)))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.m == "node" and args.n:
        result = asyncio.run(service.run_node(args.n, parse_input(args.i)))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Usage: python src/main.py -m [http|flow|node] [-i input_json] [-p port] [-n node_id]")
