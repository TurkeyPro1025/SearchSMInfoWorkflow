"""
冒烟测试：验证本地替换后三个核心环节均可正常调用
  1. FallbackSearchClient - 搜索单条资讯
  2. ChatOpenAI           - LLM 一问一答
  3. FeishuBitable        - 获取 tenant_access_token

运行方式（在项目根目录）:
  uv run python scripts/smoke_test.py
"""

import os
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


# ─── 3. 飞书 token ──────────────────────────────────────────────
print("\n[3/3] Feishu tenant_access_token")
try:
    import requests
    app_id = os.environ["FEISHU_APP_ID"]
    app_secret = os.environ["FEISHU_APP_SECRET"]
    resp = requests.post(
        "https://open.larkoffice.com/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(data)
    token = data["tenant_access_token"]
    _ok(f"token 获取成功（前16位）: {token[:16]}…")
except Exception as e:
    _fail("飞书 token 获取失败", e)

print()
