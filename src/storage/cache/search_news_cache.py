import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def get_search_news_cache_path() -> Path:
    return Path(__file__).with_name("search_news_cache.json")


def _build_cache_payload(search_news: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "search_news": search_news,
    }


def save_search_news_cache(search_news: Dict[str, Any]) -> Path:
    cache_path = get_search_news_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _build_cache_payload(search_news)
    cache_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return cache_path


def clear_search_news_cache() -> None:
    cache_path = get_search_news_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(_build_cache_payload({}), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )