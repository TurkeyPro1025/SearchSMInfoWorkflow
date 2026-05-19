import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _legacy_cache_path() -> Path:
    return _project_root() / "assets" / "workflow" / "organized_news_cache.json"


def get_organized_news_cache_path() -> Path:
    return Path(__file__).with_name("organized_news_cache.json")


def _build_cache_payload(organized_news: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "organized_news": organized_news,
    }


def save_organized_news_cache(organized_news: Dict[str, Any]) -> Path:
    cache_path = get_organized_news_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _build_cache_payload(organized_news)
    cache_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return cache_path


def load_organized_news_cache() -> Dict[str, Any]:
    cache_path = get_organized_news_cache_path()
    if not cache_path.exists():
        legacy_path = _legacy_cache_path()
        if not legacy_path.exists():
            return {}
        cache_path = legacy_path

    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    organized_news = payload.get("organized_news", {})
    return organized_news if isinstance(organized_news, dict) else {}


def clear_organized_news_cache() -> None:
    payload = _build_cache_payload({})

    for cache_path in (get_organized_news_cache_path(), _legacy_cache_path()):
        if cache_path.exists():
            cache_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )