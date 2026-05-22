import json
import logging
from typing import Any
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from tools.search_client import FallbackSearchClient, append_unique_search_results
from graphs.state import SearchBaseInput, SearchMarketEventsOutput

logger = logging.getLogger(__name__)


MARKET_EVENT_QUERY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "A股 资金面": ("a股", "大盘", "沪指", "深成指", "创业板", "北向资金", "融资余额", "两融", "杠杆", "量化", "流动性", "成交额", "放量", "缩量"),
    "A股 风格轮动": ("a股", "大盘", "沪指", "深成指", "创业板", "风格轮动", "题材", "权重", "成长", "价值", "高股息", "红利", "小盘", "科技成长"),
    "政策 监管 并购重组": ("政策", "监管", "并购重组", "ipo", "退市", "国九条", "证监会", "再融资", "科创板", "创业板"),
    "地缘政治 中东 俄乌": ("中东", "俄乌", "关税", "制裁", "地缘", "避险", "霍尔木兹", "停火", "冲突"),
    "美联储 官员讲话": ("美联储", "利率", "降息", "降准", "通胀", "就业", "官员讲话", "鲍威尔", "fomc", "点阵图"),
    "美国 CPI 非农 美债收益率": ("cpi", "非农", "美债收益率", "通胀", "就业", "利率", "美元", "pce", "失业率", "adp"),
}


def _normalize_search_text(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _filter_market_event_items(items: list[dict], query: str) -> list[dict]:
    query_keywords = MARKET_EVENT_QUERY_KEYWORDS.get(query, ())
    filtered_items: list[dict] = []
    for item in items:
        searchable_text = _normalize_search_text(
            " ".join(
                str(item.get(field, ""))
                for field in ("title", "snippet", "content")
            )
        )
        if any(keyword.lower() in searchable_text for keyword in query_keywords):
            filtered_items.append(item)
    return filtered_items


def search_market_events_node(
    state: SearchBaseInput,
    config: RunnableConfig,
    runtime: Runtime[Any],
) -> SearchMarketEventsOutput:
    """
    title: 搜索市场震荡事件资讯
    desc: 搜索A股大盘、政策变动、地缘政治、黑天鹅事件等市场震荡相关资讯，多轮查询扩大采集范围
    integrations: Web Search
    """
    client = FallbackSearchClient()

    queries: list[str] = [
        "A股 资金面",
        "A股 风格轮动",
        "政策 监管 并购重组",
        "地缘政治 中东 俄乌",
        "美联储 官员讲话",
        "美国 CPI 非农 美债收益率",
    ]

    all_results: list[dict] = []
    seen_titles: set[str] = set()
    seen_urls: set[str] = set()
    time_mode = state.search_time_mode or "rolling_24h"
    target_date = state.search_target_date or ""

    for query in queries:
        try:
            before_count = len(all_results)
            items = client.search(
                query=query,
                count=10,
                time_range=time_mode,
                target_date=target_date,
            )
            filtered_items = _filter_market_event_items(items, query)
            append_unique_search_results(
                all_results,
                filtered_items,
                seen_titles=seen_titles,
                seen_urls=seen_urls,
            )
            logger.info(
                "市场震荡 query完成 query=%s fetched=%d related_kept=%d filtered_out=%d dedup_added=%d total_after=%d",
                query,
                len(items),
                len(filtered_items),
                len(items) - len(filtered_items),
                len(all_results) - before_count,
                len(all_results),
            )
        except Exception as e:
            logger.warning("市场震荡搜索失败 query=%s error=%s", query, e)

    result_text: str = json.dumps(all_results, ensure_ascii=False, indent=2)
    logger.info("市场震荡搜索完成，共获取 %d 条去重结果", len(all_results))
    return SearchMarketEventsOutput(market_events_news=result_text)
