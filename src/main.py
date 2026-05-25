import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Dict

from tools.lark_cli_preflight import LarkCliPreflightError, ensure_lark_cli_preflight

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


def _json_ready(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def _preflight_payload(payload: Dict[str, Any]) -> None:
    ensure_lark_cli_preflight(payload.get("base_token"), payload.get("table_id"))


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
        _preflight_payload(payload)
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
        _preflight_payload(payload)
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
        runtime_stub = SimpleNamespace(context=None)

        if node_id == "search_tech_stocks":
            from graphs.nodes.search_tech_stocks_node import search_tech_stocks_node
            from graphs.state import SearchBaseInput

            node_input = SearchBaseInput(**payload)
            return search_tech_stocks_node(node_input, {}, runtime_stub)

        if node_id == "search_hk_internet":
            from graphs.nodes.search_hk_internet_node import search_hk_internet_node
            from graphs.state import SearchBaseInput

            node_input = SearchBaseInput(**payload)
            return search_hk_internet_node(node_input, {}, runtime_stub)

        if node_id == "search_commodities":
            from graphs.nodes.search_commodities_node import search_commodities_node
            from graphs.state import SearchBaseInput

            node_input = SearchBaseInput(**payload)
            return search_commodities_node(node_input, {}, runtime_stub)

        if node_id == "search_market_events":
            from graphs.nodes.search_market_events_node import search_market_events_node
            from graphs.state import SearchBaseInput

            node_input = SearchBaseInput(**payload)
            return search_market_events_node(node_input, {}, runtime_stub)

        if node_id == "organize_news":
            from graphs.nodes.organize_news_node import organize_news_node
            from graphs.state import OrganizeNewsInput

            organize_payload = self._hydrate_organize_payload(payload)
            node_input = OrganizeNewsInput(**organize_payload)
            return organize_news_node(
                node_input,
                {"metadata": {"llm_cfg": "config/organize_news_llm_cfg.json"}},
                runtime_stub,
            )

        if node_id == "write_feishu":
            _preflight_payload(payload)
            from graphs.nodes.write_feishu_node import write_feishu_node
            from graphs.state import WriteFeishuInput

            node_input = WriteFeishuInput(**payload)
            return write_feishu_node(node_input, {}, runtime_stub)

        raise NotImplementedError(f"单节点运行暂未实现: {node_id}")

    def _hydrate_organize_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if any(
            payload.get(key)
            for key in ("tech_stocks_news", "hk_internet_news", "commodities_news", "market_events_news")
        ):
            return payload

        cache_path = Path(__file__).resolve().parent / "storage" / "cache" / "search_news_cache.json"
        if not cache_path.exists():
            return payload

        try:
            cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return payload

        search_news = cache_data.get("search_news", {}) if isinstance(cache_data, dict) else {}
        if not isinstance(search_news, dict):
            return payload

        hydrated = dict(payload)
        field_map = {
            "科技股": "tech_stocks_news",
            "港股基金021378持仓": "hk_internet_news",
            "大宗商品": "commodities_news",
            "市场震荡": "market_events_news",
        }
        for category, field_name in field_map.items():
            if hydrated.get(field_name):
                continue
            items = search_news.get(category, [])
            if isinstance(items, list):
                hydrated[field_name] = json.dumps(items, ensure_ascii=False, indent=2)

        return hydrated


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
        except LarkCliPreflightError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("/run failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/stream_run")
    async def http_stream_run(request: Request) -> StreamingResponse:
        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON format") from exc

        try:
            return StreamingResponse(service.stream_sse(payload), media_type="text/event-stream")
        except LarkCliPreflightError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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
        except LarkCliPreflightError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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

    if "base_token" not in payload and "app_token" in payload:
        payload["base_token"] = payload["app_token"]

    if "base_token" not in payload:
        env_base_token = os.getenv("FEISHU_BASE_TOKEN")
        if env_base_token:
            payload["base_token"] = env_base_token

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
        print(json.dumps(_json_ready(result), ensure_ascii=False, indent=2))
    elif args.m == "node" and args.n:
        result = asyncio.run(service.run_node(args.n, parse_input(args.i)))
        print(json.dumps(_json_ready(result), ensure_ascii=False, indent=2))
    else:
        print("Usage: python src/main.py -m [http|flow|node] [-i input_json] [-p port] [-n node_id]")
