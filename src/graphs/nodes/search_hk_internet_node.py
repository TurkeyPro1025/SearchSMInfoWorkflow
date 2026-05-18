import json
import logging
from typing import Any
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from tools.search_client import FallbackSearchClient
from graphs.state import SearchBaseInput, SearchHkInternetOutput

logger = logging.getLogger(__name__)

# 基金021378（兴业中证港股通互联网指数）前十大重仓股
HOLDINGS: list[str] = [
    "腾讯控股", "阿里巴巴-W", "小米集团-W", "美团-W",
    "商汤-W", "快手-W", "京东健康", "贝壳-W",
    "金蝶国际", "哔哩哔哩-W",
]


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

    queries: list[str] = [
        "港股 互联网科技 腾讯 阿里 美团 小米 最新消息 2026",
        "港股通互联网指数 021378 持仓 重仓股 行情",
        "腾讯控股 财报 业绩 回购 最新动态",
        "阿里巴巴-W 京东健康 快手 商汤 2026",
        "港股 科网股 南向资金 持仓变动 2026",
        "小米集团 哔哩哔哩 金蝶国际 贝壳 最新消息",
    ]

    all_results: list[dict] = []
    seen_titles: set[str] = set()

    for query in queries:
        try:
            items = client.search(query=query, count=10, time_range="1d")
            for item in items:
                title = item.get("title", "") or ""
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    all_results.append(item)
        except Exception as e:
            logger.warning("港股搜索失败 query=%s error=%s", query, e)

    result_text: str = json.dumps(all_results, ensure_ascii=False, indent=2)
    logger.info("港股基金021378搜索完成，共获取 %d 条去重结果", len(all_results))
    return SearchHkInternetOutput(hk_internet_news=result_text)
