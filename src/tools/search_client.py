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
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

logger = logging.getLogger(__name__)

try:
    BEIJING_TZ = ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:
    BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")

COOLDOWN = 24 * 3600  # 配额耗尽后冷却时间（秒）
_QUOTA_STATE_FILE = os.path.join(
    os.getenv("COZE_WORKSPACE_PATH", "."), "assets", "search_quota.json"
)


class QuotaExhaustedError(Exception):
    pass


def _normalize_identity(value: Any) -> str:
    return " ".join(str(value or "").split())


_ABSOLUTE_DATE_PATTERNS = [
    re.compile(r"(20\d{2})-(\d{1,2})-(\d{1,2})"),
    re.compile(r"(20\d{2})/(\d{1,2})/(\d{1,2})"),
    re.compile(r"(20\d{2})\.(\d{1,2})\.(\d{1,2})"),
    re.compile(r"(20\d{2})年(\d{1,2})月(\d{1,2})日"),
]


def _today_date_str() -> str:
    return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")


def _now_beijing() -> datetime:
    return datetime.now(BEIJING_TZ)


def _normalize_absolute_date(match: re.Match[str]) -> str:
    year, month, day = match.groups()
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def _extract_publish_datetime(*values: Any) -> datetime | None:
    now = _now_beijing()

    for value in values:
        text = _normalize_identity(value)
        if not text:
            continue

        lowered = text.lower()
        if any(token in lowered for token in ["刚刚", "今天", "today"]):
            return now

        minute_match = re.search(r"(\d+)\s*(分钟前|minutes ago|minute ago)", lowered)
        if minute_match:
            return now - timedelta(minutes=int(minute_match.group(1)))

        hour_match = re.search(r"(\d+)\s*(小时前|hours ago|hour ago)", lowered)
        if hour_match:
            return now - timedelta(hours=int(hour_match.group(1)))

        day_match = re.search(r"(\d+)\s*(天前|days ago|day ago)", lowered)
        if day_match:
            return now - timedelta(days=int(day_match.group(1)))

        iso_datetime = text[:19]
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}[tT ]\d{2}:\d{2}:\d{2}", iso_datetime):
            try:
                return datetime.fromisoformat(iso_datetime.replace("Z", "+00:00")).astimezone(BEIJING_TZ)
            except ValueError:
                pass

        iso_candidate = text[:10]
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", iso_candidate):
            try:
                return datetime.strptime(iso_candidate, "%Y-%m-%d").replace(tzinfo=BEIJING_TZ)
            except ValueError:
                continue

        rss_match = re.search(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s+(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(20\d{2})\s+(\d{2}:\d{2}:\d{2})\s+GMT", text, re.IGNORECASE)
        if rss_match:
            try:
                parsed = datetime.strptime(rss_match.group(0), "%a, %d %b %Y %H:%M:%S GMT")
                return parsed.replace(tzinfo=timezone.utc).astimezone(BEIJING_TZ)
            except ValueError:
                pass

        for pattern in _ABSOLUTE_DATE_PATTERNS:
            match = pattern.search(text)
            if match:
                normalized = _normalize_absolute_date(match)
                try:
                    return datetime.strptime(normalized, "%Y-%m-%d").replace(tzinfo=BEIJING_TZ)
                except ValueError:
                    continue

    return None


def _normalize_publish_date(item: dict[str, Any], publish_dt: datetime) -> dict[str, Any]:
    normalized_item = dict(item)
    normalized_item["publish_date"] = publish_dt.strftime("%Y-%m-%d")
    return normalized_item


def _keep_unknown_date_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized_item = dict(item)
    normalized_item.setdefault("publish_date", "")
    return normalized_item


def _keep_today_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    today = _today_date_str()
    filtered_results: list[dict[str, Any]] = []

    for item in results:
        publish_dt = _extract_publish_datetime(
            item.get("publish_date", ""),
            item.get("title", ""),
            item.get("snippet", ""),
            item.get("content", ""),
        )
        if publish_dt is None:
            filtered_results.append(_keep_unknown_date_item(item))
            continue
        if publish_dt.strftime("%Y-%m-%d") != today:
            continue

        filtered_results.append(_normalize_publish_date(item, publish_dt))

    return filtered_results


def _keep_last_24h_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = _now_beijing()
    window_start = now - timedelta(hours=24)
    filtered_results: list[dict[str, Any]] = []

    for item in results:
        publish_dt = _extract_publish_datetime(
            item.get("publish_date", ""),
            item.get("title", ""),
            item.get("snippet", ""),
            item.get("content", ""),
        )
        if publish_dt is None:
            filtered_results.append(_keep_unknown_date_item(item))
            continue
        if publish_dt < window_start or publish_dt > now:
            continue

        filtered_results.append(_normalize_publish_date(item, publish_dt))

    return filtered_results


def _keep_target_date_results(results: list[dict[str, Any]], target_date: str) -> list[dict[str, Any]]:
    filtered_results: list[dict[str, Any]] = []

    for item in results:
        publish_dt = _extract_publish_datetime(
            item.get("publish_date", ""),
            item.get("title", ""),
            item.get("snippet", ""),
            item.get("content", ""),
        )
        if publish_dt is None:
            logger.warning("[search] 指定日期模式丢弃无可解析发布时间的结果: %s", item.get("title", ""))
            continue
        if publish_dt.strftime("%Y-%m-%d") != target_date:
            continue

        filtered_results.append(_normalize_publish_date(item, publish_dt))

    return filtered_results


def _apply_time_filter(
    results: list[dict[str, Any]],
    time_mode: str,
    target_date: str = "",
) -> list[dict[str, Any]]:
    if time_mode == "today":
        return _keep_today_results(results)
    if time_mode == "date":
        return _keep_target_date_results(results, target_date)
    return _keep_last_24h_results(results)


def _build_query(query: str, time_mode: str, target_date: str = "") -> str:
    if time_mode == "date" and target_date:
        return f"{query} {target_date}"

    return query


def append_unique_search_results(
    target_results: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
    *,
    seen_titles: set[str],
    seen_urls: set[str],
) -> None:
    for item in new_items:
        title = _normalize_identity(item.get("title", ""))
        url = _normalize_identity(item.get("url", ""))

        if (title and title in seen_titles) or (url and url in seen_urls):
            continue

        if title:
            seen_titles.add(title)
        if url:
            seen_urls.add(url)
        target_results.append(item)


class SerperSearchProvider:
    """Google Serper API（支持 qdr:h 小时级时间过滤，中文源友好）"""

    def search(self, query: str, count: int, time_range: str) -> list[dict[str, Any]]:
        tbs = "qdr:h" if time_range == "rolling_24h" else "qdr:d"
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
                "publish_date": v.get("date", ""),
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
                "publish_date": v.get("datePublished", ""),
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
                "publish_date": v.get("page_age") or v.get("age", ""),
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
                "publish_date": (
                    v.get("pagemap", {}).get("metatags", [{}])[0].get("article:published_time", "") if isinstance(v.get("pagemap", {}).get("metatags", []), list) and v.get("pagemap", {}).get("metatags", []) else "",
                ),
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
        self,
        query: str,
        count: int = 10,
        time_range: str = "rolling_24h",
        target_date: str = "",
    ) -> list[dict[str, Any]]:
        """
        执行搜索，返回 list[dict]，每项包含: title, url, snippet, content, publish_date。
        默认仅保留过去24小时结果；支持北京时间今天和指定日期过滤。
        """
        provider_query = _build_query(query, time_range, target_date)
        for name, provider in self._providers:
            if time.time() - self._state.get(name, 0) < COOLDOWN:
                logger.debug("[search] %s 冷却中，跳过", name)
                continue
            try:
                results = provider.search(provider_query, count, time_range)
                filtered_results = _apply_time_filter(results, time_range, target_date)
                logger.info(
                    "[search] provider=%s query=%s time_mode=%s target_date=%s raw_results=%d filtered_results=%d",
                    name,
                    provider_query,
                    time_range,
                    target_date or "-",
                    len(results),
                    len(filtered_results),
                )
                return filtered_results
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
