import json
import logging
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.runtime import Runtime
from langchain_openai import ChatOpenAI
from graphs.state import OrganizeNewsInput, OrganizeNewsOutput
from storage.cache.organized_news_cache import save_organized_news_cache

logger = logging.getLogger(__name__)

# 允许的领域列表，不允许修改
ALLOWED_CATEGORIES = {"科技股", "港股基金021378持仓", "大宗商品", "市场震荡"}
MAX_CANDIDATES_PER_CATEGORY = 12
MAX_TITLE_CHARS = 80
MAX_SNIPPET_CHARS = 180
MAX_CONTENT_CHARS = 220
MAX_FALLBACK_TEXT_CHARS = 3000


def _resolve_cfg_path(cfg_path: str) -> Path:
    if not cfg_path:
        raise ValueError("未找到LLM配置文件路径")

    candidate = Path(cfg_path)
    if candidate.is_absolute():
        return candidate

    project_root = Path(__file__).resolve().parents[3]
    return project_root / candidate


def _get_text_content(content: Any) -> str:
    """安全地从LLM响应中提取文本内容"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if content and isinstance(content[0], str):
            return " ".join(content)
        return " ".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return str(content)


def _normalize_text(value: Any) -> str:
    text = str(value or "")
    return " ".join(text.split())


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _domain_hint(url: str) -> str:
    hostname = urlparse(url).netloc.lower().removeprefix("www.")
    return hostname


def _compress_news_payload(raw_text: str) -> str:
    normalized = _normalize_text(raw_text)
    if not normalized:
        return ""

    try:
        parsed = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return _truncate_text(normalized, MAX_FALLBACK_TEXT_CHARS)

    if not isinstance(parsed, list):
        return _truncate_text(normalized, MAX_FALLBACK_TEXT_CHARS)

    compact_items: List[Dict[str, Any]] = []
    seen_keys: set[str] = set()

    for item in parsed:
        if not isinstance(item, dict):
            continue

        title = _truncate_text(_normalize_text(item.get("title", "")), MAX_TITLE_CHARS)
        snippet = _truncate_text(_normalize_text(item.get("snippet", "")), MAX_SNIPPET_CHARS)
        content = _truncate_text(_normalize_text(item.get("content", "")), MAX_CONTENT_CHARS)
        url = _normalize_text(item.get("url", ""))

        dedupe_key = title or url
        if not dedupe_key or dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)

        compact_item: Dict[str, Any] = {
            "title": title,
            "snippet": snippet or content,
            "url": url,
        }

        domain_hint = _domain_hint(url)
        if domain_hint:
            compact_item["source_hint"] = domain_hint
        if content and content != compact_item["snippet"]:
            compact_item["content"] = content

        compact_items.append(compact_item)
        if len(compact_items) >= MAX_CANDIDATES_PER_CATEGORY:
            break

    if not compact_items:
        return _truncate_text(normalized, MAX_FALLBACK_TEXT_CHARS)

    return json.dumps(compact_items, ensure_ascii=False, separators=(",", ":"))


def _build_compact_inputs(state: OrganizeNewsInput) -> Dict[str, str]:
    inputs = {
        "tech_stocks_news": _compress_news_payload(state.tech_stocks_news),
        "hk_internet_news": _compress_news_payload(state.hk_internet_news),
        "commodities_news": _compress_news_payload(state.commodities_news),
        "market_events_news": _compress_news_payload(state.market_events_news),
    }

    for key, value in inputs.items():
        logger.info("[organize_news] 输入压缩 %s: %d -> %d chars", key, len(getattr(state, key, "") or ""), len(value))

    return inputs


def organize_news_node(
    state: OrganizeNewsInput, config: RunnableConfig, runtime: Runtime[Any]
) -> OrganizeNewsOutput:
    """
    title: 资讯分类整理
    desc: 使用大模型将多领域搜索结果按领域分类、去重、提炼核心信息，输出结构化资讯数据
    integrations: 大语言模型
    """
    ctx = runtime.context

    # 读取LLM配置
    cfg_path = config.get("metadata", {}).get("llm_cfg", "")
    full_cfg_path = _resolve_cfg_path(cfg_path)
    with full_cfg_path.open("r", encoding="utf-8") as fd:
        _cfg = json.load(fd)

    llm_config: Dict[str, Any] = _cfg.get("config", {})
    sp: str = _cfg.get("sp", "")
    up: str = _cfg.get("up", "")

    model_name: str = llm_config.get("model", "qwen-plus-0112")
    temperature: float = llm_config.get("temperature", 0.1)
    max_completion_tokens: int = llm_config.get("max_completion_tokens", 8192)

    # 渲染用户提示词
    compact_inputs = _build_compact_inputs(state)
    up_tpl = Template(up)
    user_prompt = up_tpl.render(
        {
            "tech_stocks_news": compact_inputs["tech_stocks_news"] or "暂无科技股相关资讯",
            "hk_internet_news": compact_inputs["hk_internet_news"] or "暂无港股互联网相关资讯",
            "commodities_news": compact_inputs["commodities_news"] or "暂无大宗商品相关资讯",
            "market_events_news": compact_inputs["market_events_news"] or "暂无市场震荡事件相关资讯",
        }
    )

    messages = [
        SystemMessage(content=sp),
        HumanMessage(content=user_prompt),
    ]

    logger.info("[organize_news] 调用LLM整理资讯, model=%s", model_name)

    llm = ChatOpenAI(
        model=model_name,
        temperature=temperature,
        max_tokens=max_completion_tokens,
    )
    response = llm.invoke(messages)

    raw_text = _get_text_content(response.content)
    logger.info("[organize_news] LLM返回内容长度: %d", len(raw_text))

    # 解析LLM返回的JSON
    organized_news: Dict[str, Any] = {}
    try:
        # 尝试提取JSON块（可能包含在```json ... ```中）
        json_str = raw_text
        if "```json" in json_str:
            start_idx = json_str.index("```json") + len("```json")
            end_idx = json_str.index("```", start_idx)
            json_str = json_str[start_idx:end_idx].strip()
        elif "```" in json_str:
            start_idx = json_str.index("```") + len("```")
            end_idx = json_str.index("```", start_idx)
            json_str = json_str[start_idx:end_idx].strip()

        parsed = json.loads(json_str)
        if isinstance(parsed, dict):
            # 验证和过滤领域字段
            validated = {}
            for category, items in parsed.items():
                if category in ALLOWED_CATEGORIES:
                    validated[category] = items
                else:
                    logger.warning("[organize_news] 发现非允许领域: %s，已过滤", category)
            organized_news = validated
            logger.info("[organize_news] 领域验证完成，保留领域: %s", list(organized_news.keys()))
        else:
            logger.warning("[organize_news] LLM返回JSON非dict类型，使用原始结构")
            organized_news = {"raw_data": parsed}
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("[organize_news] JSON解析失败: %s，将原始文本存入raw_data", e)
        organized_news = {"raw_data": raw_text}

    try:
        cache_path = save_organized_news_cache(organized_news)
        logger.info("[organize_news] 已写入清洗结果缓存: %s", cache_path)
    except Exception as e:
        logger.warning("[organize_news] 写入清洗结果缓存失败: %s", e)

    return OrganizeNewsOutput(organized_news=organized_news)
