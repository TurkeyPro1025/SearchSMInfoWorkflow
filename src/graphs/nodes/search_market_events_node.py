import json
import logging
from typing import Any
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from tools.search_client import FallbackSearchClient
from graphs.state import SearchBaseInput, SearchMarketEventsOutput

logger = logging.getLogger(__name__)


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
        "A股 大盘 震荡 放量 缩量 风格轮动 最新",
        "美联储 降息 加息 CPI 非农 美债收益率 最新",
        "地缘政治 中东 俄乌 关税 制裁 全球市场 最新",
        "北向资金 两融 杠杆 量化 资金面 A股 最新",
        "A股 政策 监管 国九条 IPO 退市 并购重组 最新",
        "黑天鹅 系统性风险 VIX 汇率 债市 波动 最新",
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
            logger.warning("市场震荡搜索失败 query=%s error=%s", query, e)

    result_text: str = json.dumps(all_results, ensure_ascii=False, indent=2)
    logger.info("市场震荡搜索完成，共获取 %d 条去重结果", len(all_results))
    return SearchMarketEventsOutput(market_events_news=result_text)
