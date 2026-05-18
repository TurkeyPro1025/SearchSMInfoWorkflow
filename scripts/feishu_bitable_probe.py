#!/usr/bin/env python3
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None


def _mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:6]}...{token[-4:]}"


def _load_paths() -> None:
    project_root = Path(__file__).resolve().parents[1]
    src_dir = project_root / "src"
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="飞书多维表格鉴权/权限探针")
    parser.add_argument("--app-token", default=os.getenv("FEISHU_APP_TOKEN", ""), help="飞书 Base 的 app_token")
    parser.add_argument("--table-id", default=os.getenv("FEISHU_TABLE_ID", ""), help="飞书数据表 table_id")
    parser.add_argument("--probe-write", action="store_true", help="执行最小写入探针")
    parser.add_argument("--skip-read", action="store_true", help="跳过基础读探针")
    return parser.parse_args()


def main() -> int:
    if load_dotenv is not None:
        load_dotenv()

    _load_paths()

    from tools.feishu_bitable import FeishuBitable

    args = parse_args()
    app_token = args.app_token.strip()
    table_id = args.table_id.strip()
    if not app_token:
        raise SystemExit("缺少 app_token，请通过 --app-token 或 FEISHU_APP_TOKEN 提供")

    client = FeishuBitable()
    result = {
        "token_type": client.access_token_type,
        "token_preview": _mask_token(client.access_token),
        "app_token": app_token,
        "table_id": table_id,
        "read_probe": None,
        "write_probe": None,
    }

    if not args.skip_read:
        read_result = {
            "base_info": False,
            "fields_count": None,
        }
        try:
            client.get_base_info(app_token)
            read_result["base_info"] = True
            if table_id:
                fields_resp = client.list_fields(app_token, table_id, page_size=100)
                items = fields_resp.get("data", {}).get("items", [])
                read_result["fields_count"] = len(items)
        except Exception as exc:
            read_result["error"] = str(exc)
        result["read_probe"] = read_result

    if args.probe_write:
        if not table_id:
            result["write_probe"] = {"ok": False, "error": "缺少 table_id，无法执行写入探针"}
        else:
            title = f"permission_probe_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            try:
                resp = client.add_records(
                    app_token,
                    table_id,
                    [{"fields": {"标题": title}}],
                )
                record_items = resp.get("data", {}).get("records", [])
                result["write_probe"] = {
                    "ok": True,
                    "title": title,
                    "record_count": len(record_items),
                }
            except Exception as exc:
                result["write_probe"] = {
                    "ok": False,
                    "title": title,
                    "error": str(exc),
                }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())