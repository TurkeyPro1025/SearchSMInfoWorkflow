import json
import logging
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from coze_coding_dev_sdk import SearchClient
from graphs.state import SearchBaseInput, SearchTechStocksOutput

logger = logging.getLogger(__name__)


def search_tech_stocks_node(
    state: SearchBaseInput,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> SearchTechStocksOutput:
    """
    title: 搜索科技股资讯
    desc: 搜索存储芯片、半导体、新能源等科技股细分领域的最新资讯，执行多轮关键词查询扩大采集范围
    integrations: Web Search
    """
    ctx = runtime.context
    client = SearchClient()

    queries: list[str] = [
        "存储芯片 DRAM NAND 行情 涨价 2026",
        "半导体 先进封装 国产替代 最新消息",
        "新能源 光伏 锂电池 储能 行业动态",
        "AI算力 芯片 英伟达 国产算力 最新",
        "半导体设备 刻蚀 薄膜 离子注入 2026",
        "科技股 A股 芯片 存储板块 行情分析",
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
            logger.warning("科技股搜索失败 query=%s error=%s", query, e)

    result_text: str = json.dumps(all_results, ensure_ascii=False, indent=2)
    logger.info("科技股搜索完成，共获取 %d 条去重结果", len(all_results))
    return SearchTechStocksOutput(tech_stocks_news=result_text)
