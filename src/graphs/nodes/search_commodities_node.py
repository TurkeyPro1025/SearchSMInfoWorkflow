import json
import logging
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from coze_coding_dev_sdk import SearchClient
from graphs.state import SearchBaseInput, SearchCommoditiesOutput

logger = logging.getLogger(__name__)


def search_commodities_node(
    state: SearchBaseInput,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> SearchCommoditiesOutput:
    """
    title: 搜索大宗商品资讯
    desc: 搜索稀土、石油、黄金、铜、锂等大宗商品的最新行情与行业动态，多轮查询扩大采集范围
    integrations: Web Search
    """
    ctx = runtime.context
    client = SearchClient()

    queries: list[str] = [
        "稀土价格 北方稀土 氧化镨钕 行情 2026",
        "国际油价 WTI 布伦特原油 OPEC 最新",
        "黄金价格 国际金价 纽约金 最新行情 2026",
        "碳酸锂价格 锂矿 新能源金属 行情",
        "铜价 LME铜 沪铜 供需分析 2026",
        "大宗商品 期货 贵金属 工业金属 行情分析",
    ]

    all_results: list[dict] = []
    seen_titles: set[str] = set()

    for query in queries:
        try:
            resp = client.search(query=query, search_type="web", count=10, need_content=True, time_range="1d")
            items = resp.web_items if hasattr(resp, "web_items") else []
            for item in items:
                title = getattr(item, "title", "") or ""
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    all_results.append({
                        "title": title,
                        "snippet": getattr(item, "snippet", "") or "",
                        "url": getattr(item, "url", "") or "",
                        "content": getattr(item, "content", "") or "",
                    })
        except Exception as e:
            logger.warning("大宗商品搜索失败 query=%s error=%s", query, e)

    result_text: str = json.dumps(all_results, ensure_ascii=False, indent=2)
    logger.info("大宗商品搜索完成，共获取 %d 条去重结果", len(all_results))
    return SearchCommoditiesOutput(commodities_news=result_text)
