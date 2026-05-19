"""
冒烟测试：验证本地替换后三个核心环节均可正常调用
  1. FallbackSearchClient - 搜索单条资讯
  2. ChatOpenAI           - LLM 一问一答
    3. lark-cli             - 飞书 CLI 鉴权与 Base 只读检查

运行方式（在项目根目录）:
  uv run python scripts/smoke_test.py
"""

import os
import shutil
import subprocess
import sys

# 把 src 加入路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


def _ok(msg):
    print(f"  \033[32m✓\033[0m {msg}")

def _fail(msg, err):
    print(f"  \033[31m✗\033[0m {msg}: {err}")
    return False


# ─── 1. 搜索 ────────────────────────────────────────────────────
print("\n[1/3] FallbackSearchClient")
try:
    from tools.search_client import FallbackSearchClient
    client = FallbackSearchClient()
    if not client._providers:
        raise RuntimeError("未检测到任何搜索供应商环境变量（SERPER_API_KEY / BING_SEARCH_API_KEY / BRAVE_API_KEY / GOOGLE_CSE_API_KEY）")
    results = client.search("黄金价格 今日", count=3, time_range="1d")
    if not results:
        print("  \033[33m⚠\033[0m 搜索返回空结果（网络/配额问题？），但调用本身未报错")
    else:
        _ok(f"返回 {len(results)} 条结果，首条标题: {results[0].get('title','')[:40]}")
except Exception as e:
    _fail("搜索失败", e)


# ─── 2. LLM ─────────────────────────────────────────────────────
print("\n[2/3] ChatOpenAI (qwen-plus)")
try:
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage
    llm = ChatOpenAI(model="qwen-plus", temperature=0, max_tokens=64)
    resp = llm.invoke([HumanMessage(content="用一句话介绍黄金的投资价值")])
    content = resp.content if isinstance(resp.content, str) else str(resp.content)
    _ok(f"LLM 响应: {content[:80]}")
except Exception as e:
    _fail("LLM 调用失败", e)


# ─── 3. 飞书 CLI ────────────────────────────────────────────────
print("\n[3/3] lark-cli Feishu auth")
try:
    if shutil.which("lark-cli") is None:
        raise RuntimeError("未找到 lark-cli")

    status = subprocess.run(
        ["lark-cli", "auth", "status"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    if status.returncode != 0:
        raise RuntimeError(status.stderr.strip() or status.stdout.strip() or "auth status 失败")

    base_token = os.environ.get("FEISHU_BASE_TOKEN", "").strip()
    table_id = os.environ.get("FEISHU_TABLE_ID", "").strip()
    if base_token and table_id:
        fields = subprocess.run(
            [
                "lark-cli",
                "base",
                "+field-list",
                "--as",
                "user",
                "--base-token",
                base_token,
                "--table-id",
                table_id,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        if fields.returncode != 0:
            raise RuntimeError(fields.stderr.strip() or fields.stdout.strip() or "field-list 失败")
        _ok("lark-cli 鉴权可用，且可读取目标 Base 字段")
    else:
        _ok("lark-cli 鉴权可用；未提供 FEISHU_BASE_TOKEN 或 FEISHU_TABLE_ID，跳过 Base 只读检查")
except Exception as e:
    _fail("飞书 CLI 检查失败", e)

print()
