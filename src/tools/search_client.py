"""
FallbackSearchClient: 多供应商搜索客户端，按优先级回退。
优先级: Serper → Bing → Brave → Google CSE

环境变量:
  SERPER_API_KEY       - Google Serper API Key
  BING_SEARCH_API_KEY  - Azure Bing Search v7 Key
  BRAVE_API_KEY        - Brave Search API Key
  GOOGLE_CSE_API_KEY   - Google Custom Search API Key
  GOOGLE_CSE_ID        - Google Custom Search Engine ID
"""

import json
import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

COOLDOWN = 24 * 3600  # 配额耗尽后冷却时间（秒）
_QUOTA_STATE_FILE = os.path.join(
    os.getenv("COZE_WORKSPACE_PATH", "."), "assets", "search_quota.json"
)


class QuotaExhaustedError(Exception):
    pass


class SerperSearchProvider:
    """Google Serper API（支持 qdr:h 小时级时间过滤，中文源友好）"""

    def search(self, query: str, count: int, time_range: str) -> list[dict[str, Any]]:
        tbs = "qdr:h" if time_range == "1h" else "qdr:d"
        resp = requests.post(
            "https://google.serper.dev/news",
            headers={"X-API-KEY": os.environ["SERPER_API_KEY"], "Content-Type": "application/json"},
            json={"q": query, "num": count, "tbs": tbs, "gl": "cn", "hl": "zh-cn"},
            timeout=10,
        )
        if resp.status_code == 429:
            raise QuotaExhaustedError("Serper 429")
        resp.raise_for_status()
        return [
            {
                "title": v.get("title", ""),
                "url": v.get("link", ""),
                "snippet": v.get("snippet", ""),
                "content": "",
            }
            for v in resp.json().get("news", [])
        ]


class BingSearchProvider:
    """Azure Bing News Search v7（中文市场覆盖好）"""

    def search(self, query: str, count: int, time_range: str) -> list[dict[str, Any]]:
        resp = requests.get(
            "https://api.bing.microsoft.com/v7.0/news/search",
            headers={"Ocp-Apim-Subscription-Key": os.environ["BING_SEARCH_API_KEY"]},
            params={"q": query, "count": count, "freshness": "Day", "mkt": "zh-CN"},
            timeout=10,
        )
        if resp.status_code in (429, 403):
            raise QuotaExhaustedError(f"Bing {resp.status_code}")
        resp.raise_for_status()
        return [
            {
                "title": v.get("name", ""),
                "url": v.get("url", ""),
                "snippet": v.get("description", ""),
                "content": "",
            }
            for v in resp.json().get("value", [])
        ]


class BraveSearchProvider:
    """Brave Search API（独立索引，无 Google 依赖）"""

    def search(self, query: str, count: int, time_range: str) -> list[dict[str, Any]]:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/news/search",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": os.environ["BRAVE_API_KEY"],
            },
            params={"q": query, "count": count, "freshness": "pd", "country": "cn"},
            timeout=10,
        )
        if resp.status_code in (429, 403):
            raise QuotaExhaustedError(f"Brave {resp.status_code}")
        resp.raise_for_status()
        return [
            {
                "title": v.get("title", ""),
                "url": v.get("url", ""),
                "snippet": v.get("description", ""),
                "content": "",
            }
            for v in resp.json().get("results", [])
        ]


class GoogleCSESearchProvider:
    """Google Custom Search Engine（每日100次免费额度）"""

    def search(self, query: str, count: int, time_range: str) -> list[dict[str, Any]]:
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": os.environ["GOOGLE_CSE_API_KEY"],
                "cx": os.environ["GOOGLE_CSE_ID"],
                "q": query,
                "num": min(count, 10),
                "dateRestrict": "d1",
                "lr": "lang_zh-CN",
            },
            timeout=10,
        )
        if resp.status_code in (429, 403):
            raise QuotaExhaustedError(f"GoogleCSE {resp.status_code}")
        resp.raise_for_status()
        return [
            {
                "title": v.get("title", ""),
                "url": v.get("link", ""),
                "snippet": v.get("snippet", ""),
                "content": "",
            }
            for v in resp.json().get("items", [])
        ]


_PROVIDER_DEFS: list[tuple[str, str, type]] = [
    ("serper", "SERPER_API_KEY", SerperSearchProvider),
    ("bing", "BING_SEARCH_API_KEY", BingSearchProvider),
    ("brave", "BRAVE_API_KEY", BraveSearchProvider),
    ("google", "GOOGLE_CSE_API_KEY", GoogleCSESearchProvider),
]


class FallbackSearchClient:
    """
    多供应商搜索客户端，按优先级依次尝试，遇到配额耗尽则冷却并切换。
    只有对应环境变量存在的供应商才会被初始化。
    """

    def __init__(self) -> None:
        self._providers: list[tuple[str, Any]] = [
            (name, cls())
            for name, key, cls in _PROVIDER_DEFS
            if os.environ.get(key)
        ]
        if not self._providers:
            logger.warning("[FallbackSearchClient] 未配置任何搜索供应商环境变量，搜索将返回空列表")
        self._state: dict[str, float] = self._load_state()

    def search(
        self, query: str, count: int = 10, time_range: str = "1d"
    ) -> list[dict[str, Any]]:
        """
        执行搜索，返回 list[dict]，每项包含: title, url, snippet, content
        """
        for name, provider in self._providers:
            if time.time() - self._state.get(name, 0) < COOLDOWN:
                logger.debug("[search] %s 冷却中，跳过", name)
                continue
            try:
                results = provider.search(query, count, time_range)
                logger.debug("[search] %s 返回 %d 条结果: %s", name, len(results), query)
                return results
            except QuotaExhaustedError as e:
                logger.warning("[search] %s 配额耗尽 (%s)，切换下一供应商", name, e)
                self._state[name] = time.time()
                self._save_state()
            except Exception as e:
                logger.warning("[search] %s 请求失败: %s", name, e)

        logger.error("[search] 所有供应商均不可用，query=%s", query)
        return []

    def _load_state(self) -> dict[str, float]:
        try:
            with open(_QUOTA_STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(_QUOTA_STATE_FILE), exist_ok=True)
        with open(_QUOTA_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self._state, f)
