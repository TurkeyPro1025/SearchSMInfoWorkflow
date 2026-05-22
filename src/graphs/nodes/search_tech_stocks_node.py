import json
import logging
from typing import Any
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from tools.search_client import FallbackSearchClient, append_unique_search_results
from graphs.state import SearchBaseInput, SearchTechStocksOutput

logger = logging.getLogger(__name__)


TECH_QUERY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "存储芯片 行情": ("存储", "存储芯片", "dram", "nand", "hbm", "闪存", "内存", "内存芯片"),
    "半导体 行情": ("半导体", "芯片", "晶圆", "光刻", "刻蚀", "封装", "封测", "半导体设备"),
    "光伏 行情": ("光伏", "组件", "硅料", "逆变器", "topcon", "hjt", "bc电池", "光伏玻璃"),
    "锂电 行情": ("锂电", "碳酸锂", "电池", "动力电池", "储能电池", "盐湖", "锂盐", "正极", "负极", "隔膜", "电解液", "电芯"),
    "储能 行情": ("储能", "储能系统", "储能电池", "pack", "pcs", "bms", "逆变器", "液冷", "工商业储能"),
    "CPO 行情": ("cpo", "共封装光学", "共封装", "co-packaged optics", "光引擎", "硅光", "光模块", "光电共封装"),
    "AI算力 行情": ("ai", "ai算力", "算力", "算力租赁", "gpu", "服务器", "ai服务器", "液冷", "光模块", "cpo", "数据中心", "训练", "推理", "英伟达", "h100", "h20"),
}


def _normalize_search_text(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _filter_tech_related_items(items: list[dict], query: str) -> list[dict]:
    query_keywords = TECH_QUERY_KEYWORDS.get(query, ())
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


def search_tech_stocks_node(
    state: SearchBaseInput,
    config: RunnableConfig,
    runtime: Runtime[Any],
) -> SearchTechStocksOutput:
    """
    title: 搜索科技股资讯
    desc: 搜索存储芯片、半导体、新能源等科技股细分领域的最新资讯，执行多轮关键词查询扩大采集范围
    integrations: Web Search
    """
    client = FallbackSearchClient()

    queries: list[str] = [
        "存储芯片 行情",
        "半导体 行情",
        "光伏 行情",
        "锂电 行情",
        "储能 行情",
        "CPO 行情",
        "AI算力 行情",
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
            filtered_items = _filter_tech_related_items(items, query)
            append_unique_search_results(
                all_results,
                filtered_items,
                seen_titles=seen_titles,
                seen_urls=seen_urls,
            )
            logger.info(
                "科技股 query完成 query=%s fetched=%d related_kept=%d filtered_out=%d dedup_added=%d total_after=%d",
                query,
                len(items),
                len(filtered_items),
                len(items) - len(filtered_items),
                len(all_results) - before_count,
                len(all_results),
            )
        except Exception as e:
            logger.warning("科技股搜索失败 query=%s error=%s", query, e)

    result_text: str = json.dumps(all_results, ensure_ascii=False, indent=2)
    logger.info("科技股搜索完成，共获取 %d 条去重结果", len(all_results))
    return SearchTechStocksOutput(tech_stocks_news=result_text)
