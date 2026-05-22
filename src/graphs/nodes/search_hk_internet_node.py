import json
import logging
from typing import Any
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from tools.search_client import FallbackSearchClient, append_unique_search_results
from graphs.state import SearchBaseInput, SearchHkInternetOutput

logger = logging.getLogger(__name__)

# 基金021378（兴业中证港股通互联网指数）前十大重仓股
HOLDINGS: list[str] = [
    "腾讯控股", "阿里巴巴-W", "小米集团-W", "美团-W",
    "商汤-W", "快手-W", "京东健康", "贝壳-W",
    "金蝶国际", "哔哩哔哩-W",
]

HOLDING_ALIASES: dict[str, tuple[str, ...]] = {
    "腾讯控股": ("腾讯", "视频号", "微信", "qq", "混元", "腾讯文档", "微信搜一搜"),
    "阿里巴巴-W": ("阿里", "阿里巴巴", "淘宝", "天猫", "阿里云", "钉钉", "1688", "菜鸟"),
    "小米集团-W": ("小米", "su7", "yu7", "红米", "redmi", "小米汽车"),
    "美团-W": ("美团", "到店", "外卖", "闪购", "到店酒旅", "团购"),
    "商汤-W": ("商汤", "商汤科技", "日日新"),
    "快手-W": ("快手", "可灵", "快手电商"),
    "京东健康": ("京东健康", "京东买药", "互联网医疗"),
    "贝壳-W": ("贝壳", "链家", "居住服务", "房产交易"),
    "金蝶国际": ("金蝶", "erp", "苍穹", "星空"),
    "哔哩哔哩-W": ("哔哩", "b站", "bilibili", "up主", "游戏业务"),
}


def _normalize_search_text(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _filter_holding_related_items(items: list[dict], query: str) -> list[dict]:
    query_keywords = (query, *HOLDING_ALIASES.get(query, ()))
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

def search_hk_internet_node(
    state: SearchBaseInput,
    config: RunnableConfig,
    runtime: Runtime[Any],
) -> SearchHkInternetOutput:
    """
    title: 搜索港股基金021378持仓资讯
    desc: 搜索基金021378（兴业中证港股通互联网指数）前十大重仓股相关资讯，多轮查询扩大采集范围
    integrations: Web Search
    """
    client = FallbackSearchClient()

    queries: list[str] = list(HOLDINGS)

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
            filtered_items = _filter_holding_related_items(items, query)
            append_unique_search_results(
                all_results,
                filtered_items,
                seen_titles=seen_titles,
                seen_urls=seen_urls,
            )
            logger.info(
                "港股 query完成 query=%s 返回数=%d 命中数量=%d 过滤数量=%d 最终新增进结果集的数量=%d 累计结果数=%d",
                query,
                len(items),
                len(filtered_items),
                len(items) - len(filtered_items),
                len(all_results) - before_count,
                len(all_results),
            )
        except Exception as e:
            logger.warning("港股搜索失败 query=%s error=%s", query, e)

    result_text: str = json.dumps(all_results, ensure_ascii=False, indent=2)
    logger.info("港股基金021378搜索完成，共获取 %d 条去重结果", len(all_results))
    return SearchHkInternetOutput(hk_internet_news=result_text)
