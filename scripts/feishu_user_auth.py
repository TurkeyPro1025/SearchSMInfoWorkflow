#!/usr/bin/env python3
import argparse
import http.server
import json
import os
import secrets
import sys
import threading
import urllib.parse
import webbrowser

import requests

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None


AUTH_BASE_URL = "https://open.feishu.cn/open-apis/authen/v1"
AUTHORIZE_URL = "https://open.feishu.cn/open-apis/authen/v1/authorize"


def load_project_env() -> None:
    if load_dotenv is not None:
        load_dotenv()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="飞书用户授权辅助脚本")
    parser.add_argument("--redirect-uri", default=os.getenv("FEISHU_REDIRECT_URI", "http://127.0.0.1:8000/callback"), help="飞书应用后台配置的回调地址")
    parser.add_argument("--state", default="", help="OAuth state；不传则自动生成")
    parser.add_argument("--code", default="", help="授权回调中的 code；提供后将直接换取 token")
    parser.add_argument("--scopes", default=os.getenv("FEISHU_USER_SCOPES", ""), help="可选，仅用于展示；多个 scope 用空格分隔")
    parser.add_argument("--print-env", action="store_true", help="在换 token 成功后额外打印 .env 可直接粘贴的变量")
    parser.add_argument("--listen", action="store_true", help="启动本地 HTTP 回调监听，自动接收 code")
    parser.add_argument("--open-browser", action="store_true", help="生成授权链接后自动打开浏览器")
    parser.add_argument("--timeout", type=int, default=180, help="本地监听等待授权回调的超时时间（秒）")
    return parser.parse_args()


def build_authorize_url(app_id: str, redirect_uri: str, state: str, scopes: str) -> str:
    params = {
        "app_id": app_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if scopes.strip():
        params["scope"] = scopes.strip()
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(app_id: str, app_secret: str, code: str, redirect_uri: str) -> dict:
    response = requests.post(
        f"{AUTH_BASE_URL}/access_token",
        json={
            "grant_type": "authorization_code",
            "code": code,
            "app_id": app_id,
            "app_secret": app_secret,
            "redirect_uri": redirect_uri,
        },
        timeout=15,
    )
    return {
        "status_code": response.status_code,
        "body": response.json(),
    }


def wait_for_callback(redirect_uri: str, expected_state: str, timeout_seconds: int) -> dict:
    parsed = urllib.parse.urlparse(redirect_uri)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    callback_path = parsed.path or "/"
    result = {"code": "", "state": "", "error": "", "query": {}}
    done = threading.Event()

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            request_url = urllib.parse.urlparse(self.path)
            if request_url.path != callback_path:
                self.send_response(404)
                self.end_headers()
                self.wfile.write("Not Found".encode("utf-8"))
                return

            query = urllib.parse.parse_qs(request_url.query)
            result["code"] = query.get("code", [""])[0]
            result["state"] = query.get("state", [""])[0]
            result["error"] = query.get("error", [""])[0]
            result["query"] = {key: values[0] if len(values) == 1 else values for key, values in query.items()}

            if expected_state and result["state"] and result["state"] != expected_state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write("OAuth state mismatch".encode("utf-8"))
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    "<html><body><h2>Feishu OAuth callback received.</h2><p>可以关闭此页面，返回终端查看 token 结果。</p></body></html>".encode("utf-8")
                )
            done.set()

        def log_message(self, format: str, *args) -> None:
            return

    server = http.server.ThreadingHTTPServer((host, port), CallbackHandler)
    server.timeout = 0.5
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if not done.wait(timeout_seconds):
            raise TimeoutError(f"等待 OAuth 回调超时: {timeout_seconds}s")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    return result


def print_env_block(data: dict) -> None:
    access_token = data.get("access_token") or data.get("user_access_token") or ""
    refresh_token = data.get("refresh_token") or ""
    print("\n# .env entries")
    if access_token:
        print(f"FEISHU_USER_ACCESS_TOKEN={access_token}")
    if refresh_token:
        print(f"FEISHU_USER_REFRESH_TOKEN={refresh_token}")


def main() -> int:
    load_project_env()
    args = parse_args()

    app_id = os.getenv("FEISHU_APP_ID", "").strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        raise SystemExit("缺少 FEISHU_APP_ID 或 FEISHU_APP_SECRET")

    state = args.state.strip() or secrets.token_urlsafe(16)
    authorize_url = build_authorize_url(app_id, args.redirect_uri, state, args.scopes)

    result = {
        "app_id": app_id,
        "redirect_uri": args.redirect_uri,
        "state": state,
        "authorize_url": authorize_url,
    }

    code = args.code.strip()
    if args.listen:
        parsed = urllib.parse.urlparse(args.redirect_uri)
        if parsed.scheme != "http" or (parsed.hostname or "") not in {"127.0.0.1", "localhost"}:
            raise SystemExit("--listen 仅支持本地 http 回调地址，例如 http://127.0.0.1:8000/callback")

        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.open_browser:
            webbrowser.open(authorize_url)
        callback = wait_for_callback(args.redirect_uri, state, args.timeout)
        result["callback"] = callback
        code = callback.get("code", "").strip()

    if not code:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    exchange_result = exchange_code(app_id, app_secret, code, args.redirect_uri)
    result["exchange_result"] = exchange_result
    print(json.dumps(result, ensure_ascii=False, indent=2))

    body = exchange_result.get("body", {})
    if args.print_env and isinstance(body, dict):
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        print_env_block(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())