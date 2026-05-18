import json
import logging
from pathlib import Path
from typing import Any, Dict, List
from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.runtime import Runtime
from langchain_openai import ChatOpenAI
from graphs.state import OrganizeNewsInput, OrganizeNewsOutput

logger = logging.getLogger(__name__)

# 允许的领域列表，不允许修改
ALLOWED_CATEGORIES = {"科技股", "港股基金021378持仓", "大宗商品", "市场震荡"}


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

    model_name: str = llm_config.get("model", "doubao-seed-2-0-lite-260215")
    temperature: float = llm_config.get("temperature", 0.1)
    max_completion_tokens: int = llm_config.get("max_completion_tokens", 8192)

    # 渲染用户提示词
    up_tpl = Template(up)
    user_prompt = up_tpl.render(
        {
            "tech_stocks_news": state.tech_stocks_news or "暂无科技股相关资讯",
            "hk_internet_news": state.hk_internet_news or "暂无港股互联网相关资讯",
            "commodities_news": state.commodities_news or "暂无大宗商品相关资讯",
            "market_events_news": state.market_events_news or "暂无市场震荡事件相关资讯",
        }
    )

    messages = [
        SystemMessage(content=sp),
        HumanMessage(content=user_prompt),
    ]

    logger.info("[organize_news] 调用LLM整理资讯, model=%s", model_name)

    llm = ChatOpenAI(
        model=model_name,
        temperature=temperature,
        max_tokens=max_completion_tokens,
    )
    response = llm.invoke(messages)

    raw_text = _get_text_content(response.content)
    logger.info("[organize_news] LLM返回内容长度: %d", len(raw_text))

    # 解析LLM返回的JSON
    organized_news: Dict[str, Any] = {}
    try:
        # 尝试提取JSON块（可能包含在```json ... ```中）
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
            # 验证和过滤领域字段
            validated = {}
            for category, items in parsed.items():
                if category in ALLOWED_CATEGORIES:
                    validated[category] = items
                else:
                    logger.warning("[organize_news] 发现非允许领域: %s，已过滤", category)
            organized_news = validated
            logger.info("[organize_news] 领域验证完成，保留领域: %s", list(organized_news.keys()))
        else:
            logger.warning("[organize_news] LLM返回JSON非dict类型，使用原始结构")
            organized_news = {"raw_data": parsed}
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("[organize_news] JSON解析失败: %s，将原始文本存入raw_data", e)
        organized_news = {"raw_data": raw_text}

    return OrganizeNewsOutput(organized_news=organized_news)
