import json
import logging
from typing import Any
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from tools.search_client import FallbackSearchClient, append_unique_search_results
from graphs.state import SearchBaseInput, SearchCommoditiesOutput

logger = logging.getLogger(__name__)


COMMODITY_QUERY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "稀土 行情": ("稀土", "氧化镨钕", "镨钕", "稀土永磁", "北方稀土"),
    "原油 行情": ("原油", "石油", "油价", "成品油", "wti", "布伦特", "opec", "opec+", "页岩油"),
    "黄金 行情": ("黄金", "金价", "纽约金", "伦敦金", "现货黄金", "贵金属", "白银", "comex"),
    "碳酸锂 行情": ("碳酸锂", "电池级碳酸锂", "锂矿", "盐湖", "锂盐", "氢氧化锂", "锂辉石"),
    "铜价 行情": ("铜", "铜价", "沪铜", "伦铜", "lme", "电解铜", "铜精矿"),
    "有色金属 行情": ("有色金属", "化工", "甲醇", "纯碱", "pta", "铜", "铝", "氧化铝", "锌", "镍", "锡"),
}


def _normalize_search_text(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _filter_commodity_related_items(items: list[dict], query: str) -> list[dict]:
    query_keywords = COMMODITY_QUERY_KEYWORDS.get(query, ())
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


def search_commodities_node(
    state: SearchBaseInput,
    config: RunnableConfig,
    runtime: Runtime[Any],
) -> SearchCommoditiesOutput:
    """
    title: 搜索大宗商品资讯
    desc: 搜索稀土、石油、黄金、铜、锂等大宗商品的最新行情与行业动态，多轮查询扩大采集范围
    integrations: Web Search
    """
    client = FallbackSearchClient()

    queries: list[str] = [
        "稀土 行情",
        "原油 行情",
        "黄金 行情",
        "碳酸锂 行情",
        "铜价 行情",
        "有色金属 行情",
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
            filtered_items = _filter_commodity_related_items(items, query)
            append_unique_search_results(
                all_results,
                filtered_items,
                seen_titles=seen_titles,
                seen_urls=seen_urls,
            )
            logger.info(
                "大宗商品 query完成 query=%s fetched=%d related_kept=%d filtered_out=%d dedup_added=%d total_after=%d",
                query,
                len(items),
                len(filtered_items),
                len(items) - len(filtered_items),
                len(all_results) - before_count,
                len(all_results),
            )
        except Exception as e:
            logger.warning("大宗商品搜索失败 query=%s error=%s", query, e)

    result_text: str = json.dumps(all_results, ensure_ascii=False, indent=2)
    logger.info("大宗商品搜索完成，共获取 %d 条去重结果", len(all_results))
    return SearchCommoditiesOutput(commodities_news=result_text)
