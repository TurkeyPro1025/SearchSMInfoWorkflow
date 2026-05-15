from langgraph.graph import StateGraph, START, END

from graphs.state import GlobalState, GraphInput, GraphOutput
from graphs.nodes.search_tech_stocks_node import search_tech_stocks_node
from graphs.nodes.search_hk_internet_node import search_hk_internet_node
from graphs.nodes.search_commodities_node import search_commodities_node
from graphs.nodes.search_market_events_node import search_market_events_node
from graphs.nodes.organize_news_node import organize_news_node
from graphs.nodes.write_feishu_node import write_feishu_node

# 创建状态图，指定全局状态、入参和出参
builder = StateGraph(GlobalState, input_schema=GraphInput, output_schema=GraphOutput)

# ==================== 添加节点 ====================

# 4个并行搜索节点
builder.add_node("search_tech_stocks", search_tech_stocks_node)
builder.add_node("search_hk_internet", search_hk_internet_node)
builder.add_node("search_commodities", search_commodities_node)
builder.add_node("search_market_events", search_market_events_node)

# LLM资讯整理节点（Agent节点，注入模型配置）
builder.add_node(
    "organize_news",
    organize_news_node,
    metadata={"type": "agent", "llm_cfg": "config/organize_news_llm_cfg.json"},
)

# 飞书多维表格写入节点
builder.add_node("write_feishu", write_feishu_node)

# ==================== 设置并行入口 ====================
# 从START同时触发4路搜索，实现真正的并行
builder.add_edge(START, "search_tech_stocks")
builder.add_edge(START, "search_hk_internet")
builder.add_edge(START, "search_commodities")
builder.add_edge(START, "search_market_events")

# ==================== 添加汇聚边 ====================
# 4路搜索全部完成后，汇聚到资讯整理节点
builder.add_edge(
    ["search_tech_stocks", "search_hk_internet", "search_commodities", "search_market_events"],
    "organize_news",
)

# ==================== 添加后续边 ====================
builder.add_edge("organize_news", "write_feishu")
builder.add_edge("write_feishu", END)

# ==================== 编译图 ====================
main_graph = builder.compile()
