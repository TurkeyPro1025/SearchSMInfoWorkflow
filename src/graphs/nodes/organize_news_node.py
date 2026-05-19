import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.runtime import Runtime
from langchain_openai import ChatOpenAI
from graphs.state import OrganizeNewsInput, OrganizeNewsOutput
from storage.cache.organized_news_cache import clear_organized_news_cache, save_organized_news_cache

logger = logging.getLogger(__name__)

# 允许的领域列表，不允许修改
ALLOWED_CATEGORIES = {"科技股", "港股基金021378持仓", "大宗商品", "市场震荡"}
MAX_TITLE_CHARS = 80
MAX_SNIPPET_CHARS = 180
MAX_CONTENT_CHARS = 220
MAX_FALLBACK_TEXT_CHARS = 3000

DATE_PATTERN = re.compile(r"(20\d{2})[年\-/.](\d{1,2})(?:[月\-/.](\d{1,2}))?")

CATEGORY_ENTITY_KEYWORDS: Dict[str, Dict[str, tuple[str, ...]]] = {
    "科技股": {
        "存储芯片": ("存储", "dram", "nand", "hbm", "闪存", "内存"),
        "半导体": ("半导体", "芯片", "晶圆", "光刻", "刻蚀", "封装"),
        "光伏": ("光伏", "组件", "硅料", "逆变器"),
        "锂电池": ("锂电", "碳酸锂", "电池", "储能", "盐湖"),
        "ai算力": ("ai", "算力", "gpu", "服务器", "液冷", "光模块"),
        "消费电子": ("消费电子", "手机", "pc", "可穿戴", "面板"),
    },
    "港股基金021378持仓": {
        "腾讯控股": ("腾讯", "视频号", "微信", "qq"),
        "阿里巴巴": ("阿里", "淘宝", "天猫", "阿里云"),
        "小米集团": ("小米", "su7", "红米"),
        "美团": ("美团", "到店", "外卖", "闪购"),
        "商汤": ("商汤",),
        "快手": ("快手",),
        "京东健康": ("京东健康",),
        "贝壳": ("贝壳",),
        "金蝶国际": ("金蝶",),
        "哔哩哔哩": ("哔哩", "b站", "bilibili"),
    },
    "大宗商品": {
        "稀土": ("稀土", "氧化镨钕", "北方稀土"),
        "原油": ("原油", "wti", "布伦特", "opec"),
        "黄金": ("黄金", "金价", "纽约金"),
        "碳酸锂": ("碳酸锂", "锂矿", "盐湖", "锂盐"),
        "铜": ("铜", "沪铜", "lme"),
        "化工": ("化工", "甲醇", "纯碱", "pta"),
    },
    "市场震荡": {
        "a股大盘": ("a股", "大盘", "沪指", "深成指", "创业板"),
        "美联储": ("美联储", "cpi", "非农", "美债收益率"),
        "地缘政治": ("中东", "俄乌", "关税", "制裁", "地缘"),
        "资金面": ("北向资金", "两融", "杠杆", "量化"),
        "监管政策": ("ipo", "退市", "并购重组", "监管", "国九条"),
        "系统性风险": ("黑天鹅", "vix", "汇率", "债市"),
    },
}

CATALYST_KEYWORDS: Dict[str, tuple[str, ...]] = {
    "财报业绩": ("财报", "业绩", "预告", "指引", "盈利"),
    "回购分红": ("回购", "分红", "增持", "减持"),
    "政策监管": ("政策", "监管", "国九条", "补贴", "配额", "关税", "制裁"),
    "价格波动": ("涨价", "跌价", "价格", "金价", "油价", "报价"),
    "供给产能": ("供给", "产能", "排产", "开工", "库存", "产量", "出口"),
    "需求销量": ("需求", "销量", "订单", "出货", "装机", "消费"),
    "资金估值": ("资金", "估值", "北向", "两融", "回流"),
    "地缘宏观": ("地缘", "美联储", "cpi", "非农", "避险", "宏观"),
}


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


def _extract_event_date(value: Any) -> str:
    text = _normalize_text(value)
    if not text:
        return "未知日期"

    match = DATE_PATTERN.search(text)
    if not match:
        return "未知日期"

    year, month, day = match.groups()
    normalized = f"{int(year):04d}-{int(month):02d}"
    if day is not None:
        normalized += f"-{int(day):02d}"
    return normalized


def _detect_anchor(category: str, text: str, fallback: str = "") -> str:
    lowered = text.lower()
    for anchor, keywords in CATEGORY_ENTITY_KEYWORDS.get(category, {}).items():
        if any(keyword.lower() in lowered for keyword in keywords):
            return anchor
    return _normalize_text(fallback) or "未识别主体"


def _detect_primary_catalyst(text: str) -> str:
    lowered = text.lower()
    for catalyst, keywords in CATALYST_KEYWORDS.items():
        if any(keyword.lower() in lowered for keyword in keywords):
            return catalyst
    return "未识别催化"


def _load_source_items(raw_text: str) -> List[Dict[str, Any]]:
    try:
        parsed = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def _build_event_item_detail(category: str, item: Dict[str, Any]) -> Dict[str, str]:
    title = _normalize_text(item.get("title", ""))
    snippet = _normalize_text(item.get("snippet", ""))
    content = _normalize_text(item.get("content", ""))
    summary = _normalize_text(item.get("summary", ""))
    industry = _normalize_text(item.get("industry", ""))
    publish_date = _normalize_text(item.get("publish_date", ""))
    url = _normalize_text(item.get("url", ""))

    text = " ".join(part for part in [title, snippet, content, summary, industry] if part)
    return {
        "title": title or "未命名资讯",
        "date": _extract_event_date(publish_date or text),
        "catalyst": _detect_primary_catalyst(text),
        "anchor": _detect_anchor(category, text, fallback=industry),
        "url": url,
    }


def _collect_event_records(category: str, items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, str]]]:
    records_by_anchor: Dict[str, Dict[str, Dict[str, str]]] = {}

    for item in items:
        detail = _build_event_item_detail(category, item)
        anchor = detail["anchor"]
        signature = f"{detail['date']}|{detail['catalyst']}"
        records_by_anchor.setdefault(anchor, {})
        records_by_anchor[anchor].setdefault(signature, detail)

    return records_by_anchor


def _collect_event_signatures(category: str, items: List[Dict[str, Any]], *, output_mode: bool) -> Dict[str, set[str]]:
    signatures_by_anchor: Dict[str, set[str]] = {}

    for item in items:
        title = _normalize_text(item.get("title", ""))
        snippet = _normalize_text(item.get("snippet", ""))
        content = _normalize_text(item.get("content", ""))
        summary = _normalize_text(item.get("summary", ""))
        industry = _normalize_text(item.get("industry", ""))
        publish_date = _normalize_text(item.get("publish_date", ""))

        text = " ".join(part for part in [title, snippet, content, summary, industry] if part)
        anchor = _detect_anchor(category, text, fallback=industry)
        date_value = _extract_event_date(publish_date or text)
        catalyst = _detect_primary_catalyst(text)
        signature = f"{date_value}|{catalyst}"

        signatures_by_anchor.setdefault(anchor, set()).add(signature)

        if output_mode and anchor == "未识别主体":
            logger.info("[organize_news] 输出校验存在未识别主体 category=%s title=%s", category, title)

    return signatures_by_anchor


def _detect_potential_event_merge_issues(
    state: OrganizeNewsInput,
    organized_news: Dict[str, Any],
) -> List[str]:
    details = _detect_potential_event_merge_details(state, organized_news)
    return [detail["message"] for detail in details]


def _detect_potential_event_merge_details(
    state: OrganizeNewsInput,
    organized_news: Dict[str, Any],
) -> List[Dict[str, Any]]:
    source_payloads = {
        "科技股": state.tech_stocks_news,
        "港股基金021378持仓": state.hk_internet_news,
        "大宗商品": state.commodities_news,
        "市场震荡": state.market_events_news,
    }

    issues: List[Dict[str, Any]] = []
    for category, raw_text in source_payloads.items():
        source_items = _load_source_items(raw_text)
        output_items = organized_news.get(category, [])
        if not isinstance(output_items, list):
            continue

        source_signatures = _collect_event_signatures(category, source_items, output_mode=False)
        output_signatures = _collect_event_signatures(category, output_items, output_mode=True)
        source_records = _collect_event_records(category, source_items)
        output_records = _collect_event_records(category, output_items)

        for anchor, source_events in source_signatures.items():
            if anchor == "未识别主体" or len(source_events) <= 1:
                continue

            output_event_count = len(output_signatures.get(anchor, set()))
            if output_event_count >= len(source_events):
                continue

            source_examples = list(source_records.get(anchor, {}).values())[:2]
            output_examples = list(output_records.get(anchor, {}).values())[:2]
            issues.append(
                {
                    "category": category,
                    "anchor": anchor,
                    "message": f"{category}/{anchor}: 输入识别到 {len(source_events)} 个不同事件信号，但输出仅保留 {output_event_count} 条，可能发生误合并",
                    "source_examples": source_examples,
                    "output_examples": output_examples,
                }
            )

    return issues


def _compress_news_payload(raw_text: str, category: str) -> str:
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

    if not compact_items:
        return _truncate_text(normalized, MAX_FALLBACK_TEXT_CHARS)

    return json.dumps(compact_items, ensure_ascii=False, separators=(",", ":"))


def _build_compact_inputs(state: OrganizeNewsInput) -> Dict[str, str]:
    inputs = {
        "tech_stocks_news": _compress_news_payload(state.tech_stocks_news, "科技股"),
        "hk_internet_news": _compress_news_payload(state.hk_internet_news, "港股基金021378持仓"),
        "commodities_news": _compress_news_payload(state.commodities_news, "大宗商品"),
        "market_events_news": _compress_news_payload(state.market_events_news, "市场震荡"),
    }

    for key, value in inputs.items():
        logger.info("[organize_news] 输入压缩 %s: %d -> %d chars", key, len(getattr(state, key, "") or ""), len(value))

    return inputs


def _parse_llm_json(raw_text: str) -> Dict[str, Any]:
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
        validated = {}
        for category, items in parsed.items():
            if category in ALLOWED_CATEGORIES:
                validated[category] = items
            else:
                logger.warning("[organize_news] 发现非允许领域: %s，已过滤", category)
        logger.info("[organize_news] 领域验证完成，保留领域: %s", list(validated.keys()))
        return validated

    logger.warning("[organize_news] LLM返回JSON非dict类型，使用原始结构")
    return {"raw_data": parsed}


def _invoke_organize_llm(
    llm: ChatOpenAI,
    system_prompt: str,
    user_prompt: str,
) -> tuple[Dict[str, Any], str]:
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]
    response = llm.invoke(messages)
    raw_text = _get_text_content(response.content)
    logger.info("[organize_news] LLM返回内容长度: %d", len(raw_text))

    try:
        return _parse_llm_json(raw_text), raw_text
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("[organize_news] JSON解析失败: %s，将原始文本存入raw_data", e)
        return {"raw_data": raw_text}, raw_text


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

    logger.info("[organize_news] 调用LLM整理资讯, model=%s", model_name)

    llm = ChatOpenAI(
        model=model_name,
        temperature=temperature,
        max_tokens=max_completion_tokens,
    )
    organized_news, raw_text = _invoke_organize_llm(llm, sp, user_prompt)

    if "raw_data" not in organized_news:
        merge_issue_details = _detect_potential_event_merge_details(state, organized_news)
        for issue in merge_issue_details:
            source_examples = issue.get("source_examples", []) if isinstance(issue, dict) else []
            titles = [example.get("title", "") for example in source_examples if isinstance(example, dict) and example.get("title")]
            if len(titles) >= 2:
                logger.warning(
                    "[organize_news] 潜在误合并提醒: 领域=%s 主体=%s 标题1=%s 标题2=%s",
                    issue.get("category", ""),
                    issue.get("anchor", ""),
                    titles[0],
                    titles[1],
                )
            else:
                logger.warning("[organize_news] 输出后校验发现潜在误合并: %s", issue.get("message", issue))

    try:
        clear_organized_news_cache()
        logger.info("[organize_news] 写入新清洗结果前已清空旧缓存内容")
        cache_path = save_organized_news_cache(organized_news)
        logger.info("[organize_news] 已写入清洗结果缓存: %s", cache_path)
    except Exception as e:
        logger.warning("[organize_news] 写入清洗结果缓存失败: %s", e)

    return OrganizeNewsOutput(organized_news=organized_news)
