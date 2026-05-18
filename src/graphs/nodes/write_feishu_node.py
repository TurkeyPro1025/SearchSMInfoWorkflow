import logging
import json
import datetime
from typing import Any, Dict, List
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from graphs.state import WriteFeishuInput, WriteFeishuOutput
from tools.feishu_bitable import FeishuBitable

logger = logging.getLogger(__name__)

# 飞书表格字段定义（用于 create_table 一次性创建完整字段结构）
FIELD_DEFINITIONS: List[Dict[str, Any]] = [
    {"field_name": "领域", "type": 3, "property": {"options": [
        {"name": "科技股"}, {"name": "港股基金021378持仓"},
        {"name": "大宗商品"}, {"name": "市场震荡"},
    ]}},
    {"field_name": "标题", "type": 1},
    {"field_name": "行业", "type": 1},
    {"field_name": "内容摘要", "type": 1},
    {"field_name": "影响", "type": 3, "property": {"options": [
        {"name": "好"}, {"name": "坏"},
    ]}},
    {"field_name": "来源", "type": 1},
    {"field_name": "重要性", "type": 3, "property": {"options": [
        {"name": "高"}, {"name": "中"}, {"name": "低"},
    ]}},
    {"field_name": "链接", "type": 15},
    {"field_name": "发布日期", "type": 5, "property": {"date_format": "yyyy-MM-dd"}},
    {"field_name": "预测准确率", "type": 2, "property": {"formatter": "0%"}},
    {"field_name": "真实性评估", "type": 3, "property": {"options": [
        {"name": "高"}, {"name": "中"}, {"name": "低"},
    ]}},
]

FIELD_ALIASES: Dict[str, List[str]] = {
    "领域": ["领域"],
    "标题": ["标题"],
    "行业": ["行业", "所属行业"],
    "内容摘要": ["内容摘要", "摘要"],
    "影响": ["影响"],
    "来源": ["来源"],
    "重要性": ["重要性"],
    "链接": ["链接", "原文链接"],
    "发布日期": ["发布日期", "时间", "日期", "发布时间"],
    "预测准确率": ["预测准确率"],
    "真实性评估": ["真实性评估", "可信度"],
}


def _get_current_timestamp_ms() -> int:
    return int(datetime.datetime.now().timestamp() * 1000)


def _get_today_start_ms() -> int:
    """返回当天 00:00:00 的 Unix 毫秒时间戳，用于日期字段的兜底值。"""
    today = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return int(today.timestamp() * 1000)


def _parse_date_to_timestamp_ms(date_str: str, fallback_ms: int) -> int:
    """
    将日期字符串解析为 Unix 毫秒时间戳。
    支持格式: YYYY-MM-DD, YYYY-MM-DD HH:MM:SS, YYYY-MM-DDTHH:MM:SS
    解析失败时返回 fallback_ms。
    """
    if not date_str or not isinstance(date_str, str):
        return fallback_ms

    date_str = date_str.strip()
    if not date_str:
        return fallback_ms

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            dt = datetime.datetime.strptime(date_str, fmt)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue

    logger.warning("日期解析失败，使用写入日期: %s", date_str)
    return fallback_ms


def _parse_accuracy(value: Any) -> float:
    """解析预测准确率，返回 0.0~1.0 之间的浮点数"""
    if isinstance(value, (int, float)):
        val = float(value)
        return val / 100.0 if val > 1.0 else val
    if isinstance(value, str):
        val = value.strip().replace("%", "").replace("％", "")
        try:
            num = float(val)
            return num / 100.0 if num > 1.0 else num
        except ValueError:
            return 0.0
    return 0.0


def _infer_industry(category: str, title: str, summary: str) -> str:
    text = f"{title} {summary}".lower()

    keyword_mapping = [
        ("存储", "存储芯片"),
        ("半导体", "半导体"),
        ("芯片", "半导体"),
        ("ai", "人工智能"),
        ("算力", "AI算力"),
        ("云", "云计算"),
        ("阿里", "港股互联网"),
        ("腾讯", "港股互联网"),
        ("美团", "港股互联网"),
        ("小米", "消费电子"),
        ("石油", "原油"),
        ("原油", "原油"),
        ("黄金", "黄金"),
        ("锂", "锂"),
        ("铜", "铜"),
        ("稀土", "稀土"),
        ("通胀", "宏观经济"),
        ("美债", "宏观经济"),
        ("加息", "宏观经济"),
    ]
    for keyword, industry in keyword_mapping:
        if keyword in text:
            return industry

    category_mapping = {
        "科技股": "科技",
        "港股基金021378持仓": "港股互联网",
        "大宗商品": "大宗商品",
        "市场震荡": "宏观市场",
    }
    return category_mapping.get(category, category)


def _normalize_impact(value: Any, title: str, summary: str) -> str:
    if isinstance(value, str):
        text = value.strip()
        if text in {"好", "坏"}:
            return text
        if any(token in text for token in ["利好", "正面", "受益", "上涨", "改善", "增长"]):
            return "好"
        if any(token in text for token in ["利空", "负面", "承压", "下跌", "回调", "恶化"]):
            return "坏"

    combined_text = f"{title} {summary}"
    positive_tokens = ["利好", "受益", "上涨", "增长", "改善", "修复", "突破", "景气", "反弹"]
    negative_tokens = ["利空", "下跌", "承压", "回调", "下挫", "风险", "恶化", "通缩", "抛售"]
    positive_score = sum(token in combined_text for token in positive_tokens)
    negative_score = sum(token in combined_text for token in negative_tokens)
    return "坏" if negative_score > positive_score else "好"


def _normalize_authenticity(value: Any) -> str:
    """将真实性评估统一规范为飞书单选字段可用的值。"""
    if isinstance(value, dict):
        level = value.get("level", "")
        if isinstance(level, str):
            level = level.strip()
            if level in {"高", "中", "低"}:
                return level

        publisher_authority = value.get("publisher_authority", "")
        if isinstance(publisher_authority, str):
            publisher_authority = publisher_authority.strip()
            if publisher_authority in {"高", "中", "低"}:
                return publisher_authority

        return "中"

    if isinstance(value, str):
        text = value.strip()
        if text.startswith("高"):
            return "高"
        if text.startswith("中"):
            return "中"
        if text.startswith("低"):
            return "低"

    return "中"


def _flatten_organized_news(organized_news: Dict[str, Any], collect_timestamp_ms: int) -> List[Dict[str, Any]]:
    """将分类整理后的资讯扁平化为飞书多维表格记录列表"""
    records: List[Dict[str, Any]] = []
    if not organized_news:
        return records

    for category, items in organized_news.items():
        if category == "raw_data":
            continue
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    title = item.get("title", "") or item.get("标题", "")
                    industry = item.get("industry", "") or item.get("行业", "") or item.get("所属行业", "")
                    summary = item.get("summary", "") or item.get("内容摘要", "") or item.get("摘要", "")
                    impact = item.get("impact", "") or item.get("影响", "")
                    source = item.get("source", "") or item.get("来源", "")
                    importance = item.get("importance", "") or item.get("重要性", "中")
                    url = item.get("url", "") or item.get("链接", "")
                    publish_date_str = item.get("publish_date", "") or item.get("发布日期", "") or item.get("time", "")
                    accuracy_raw = item.get("prediction_accuracy", "") or item.get("预测准确率", "")
                    authenticity = item.get("authenticity", "") or item.get("真实性评估", "") or item.get("credibility", "")

                    # 日期：优先使用发布日期，解析失败则用写入日期
                    publish_ts = _parse_date_to_timestamp_ms(publish_date_str, collect_timestamp_ms)
                    # 预测准确率：转为 0~1 浮点数
                    accuracy_val = _parse_accuracy(accuracy_raw) if accuracy_raw else 0.0
                    authenticity_value = _normalize_authenticity(authenticity)
                    industry_value = str(industry).strip() if industry else _infer_industry(category, str(title), str(summary))
                    impact_value = _normalize_impact(impact, str(title), str(summary))

                    record_fields: Dict[str, Any] = {
                        "领域": category,
                        "标题": str(title) if title else "",
                        "行业": industry_value,
                        "内容摘要": str(summary) if summary else "",
                        "影响": impact_value,
                        "来源": str(source) if source else "",
                        "重要性": str(importance) if importance else "中",
                        "链接": {"link": str(url), "text": str(title) or str(url)} if url else {"link": "", "text": ""},
                        "发布日期": publish_ts,
                        "预测准确率": accuracy_val,
                        "真实性评估": authenticity_value,
                    }
                    records.append({"fields": record_fields})
                elif isinstance(item, str):
                    records.append({"fields": {
                        "领域": category, "标题": item, "行业": _infer_industry(category, item, ""), "内容摘要": "",
                        "影响": _normalize_impact("", item, ""),
                        "来源": "", "重要性": "中", "链接": {"link": "", "text": ""},
                        "发布日期": collect_timestamp_ms, "预测准确率": 0.0,
                        "真实性评估": "低",
                    }})
        elif isinstance(items, str):
            records.append({"fields": {
                "领域": category, "标题": items, "行业": _infer_industry(category, items, ""), "内容摘要": "",
                "影响": _normalize_impact("", items, ""),
                "来源": "", "重要性": "中", "链接": {"link": "", "text": ""},
                "发布日期": collect_timestamp_ms, "预测准确率": 0.0,
                "真实性评估": "低",
            }})

    return records


def _resolve_field_name_map(bitable: FeishuBitable, app_token: str, table_id: str) -> Dict[str, str]:
    available_fields = {
        item.get("field_name", "")
        for item in bitable.list_fields(app_token=app_token, table_id=table_id).get("data", {}).get("items", [])
        if item.get("field_name")
    }

    field_name_map: Dict[str, str] = {}
    for canonical_name, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if alias in available_fields:
                field_name_map[canonical_name] = alias
                break
        else:
            if canonical_name in available_fields:
                field_name_map[canonical_name] = canonical_name

    return field_name_map


def _remap_record_fields(records: List[Dict[str, Any]], field_name_map: Dict[str, str]) -> List[Dict[str, Any]]:
    remapped_records: List[Dict[str, Any]] = []

    for record in records:
        fields = record.get("fields", {})
        remapped_fields: Dict[str, Any] = {}
        for canonical_name, value in fields.items():
            target_name = field_name_map.get(canonical_name)
            if not target_name:
                continue
            remapped_fields[target_name] = value
        remapped_records.append({"fields": remapped_fields})

    return remapped_records


def _create_table_with_fields(bitable: FeishuBitable, app_token: str) -> str:
    """
    在已有 Base 中自动创建带完整字段的数据表。
    返回新建表的 table_id。
    """
    today_str = datetime.datetime.now().strftime("%Y%m%d")
    table_name = f"股市资讯_{today_str}"
    logger.info("[write_feishu] 自动创建数据表: %s, 含 %d 个预定义字段", table_name, len(FIELD_DEFINITIONS))

    table_resp = bitable.create_table(app_token=app_token, table_name=table_name, fields=FIELD_DEFINITIONS)
    table_data = table_resp.get("data", {})
    table_id = table_data.get("table_id", "")
    if not table_id:
        raise Exception(f"创建数据表失败，未获取到 table_id: {table_resp}")

    logger.info("[write_feishu] 数据表创建成功, table_id=%s", table_id)
    return table_id


def write_feishu_node(
    state: WriteFeishuInput, config: RunnableConfig, runtime: Runtime[Any]
) -> WriteFeishuOutput:
    """
    title: 写入飞书多维表格
    desc: 在已有 Base 中自动创建带完整字段的数据表（领域/标题/内容摘要/来源/重要性/链接/发布日期/预测准确率/真实性评估），并将整理后的股市资讯批量写入
    integrations: 飞书多维表格
    """
    app_token = state.app_token
    table_id = (state.table_id or "").strip()
    organized_news = state.organized_news

    if not app_token:
        error_msg = "缺少必填参数 app_token，请提供飞书多维表格的 app_token"
        logger.error("[write_feishu] %s", error_msg)
        return WriteFeishuOutput(write_result=error_msg, app_token="", table_id="")

    # 初始化飞书客户端
    try:
        bitable = FeishuBitable()
    except Exception as e:
        error_msg = f"飞书客户端初始化失败: {e}"
        logger.error("[write_feishu] %s", error_msg)
        return WriteFeishuOutput(write_result=error_msg, app_token=app_token, table_id="")

    # ========== 自动创建数据表（如果未提供 table_id）==========
    if not table_id:
        try:
            table_id = _create_table_with_fields(bitable, app_token)
            logger.info("[write_feishu] 自动创建数据表成功: table_id=%s", table_id)
        except Exception as e:
            error_msg = f"自动创建数据表失败: {e}"
            logger.error("[write_feishu] %s", error_msg)
            return WriteFeishuOutput(write_result=error_msg, app_token=app_token, table_id="")
    else:
        logger.info("[write_feishu] 使用现有数据表: table_id=%s", table_id)

    # ========== 转换资讯数据为飞书记录 ==========
    collect_timestamp_ms = _get_today_start_ms()
    records = _flatten_organized_news(organized_news, collect_timestamp_ms)
    if not records:
        logger.info("[write_feishu] 无有效资讯可写入")
        return WriteFeishuOutput(
            write_result="本次无新资讯需写入飞书多维表格",
            app_token=app_token, table_id=table_id
        )

    logger.info("[write_feishu] 准备写入 %d 条记录 (app_token=%s, table_id=%s)", len(records), app_token, table_id)
    if records:
        logger.info("[write_feishu] 第一条记录示例: %s", json.dumps(records[0], ensure_ascii=False))

    try:
        field_name_map = _resolve_field_name_map(bitable, app_token, table_id)
        records = _remap_record_fields(records, field_name_map)
        logger.info("[write_feishu] 字段映射: %s", json.dumps(field_name_map, ensure_ascii=False))
    except Exception as e:
        error_msg = f"获取飞书字段映射失败: {e}"
        logger.error("[write_feishu] %s", error_msg)
        return WriteFeishuOutput(write_result=error_msg, app_token=app_token, table_id=table_id)

    # ========== 批量写入记录 ==========
    batch_size = 500
    total_written: int = 0
    error_messages: List[str] = []

    for i in range(0, len(records), batch_size):
        batch = records[i: i + batch_size]
        try:
            resp_data = bitable.add_records(app_token=app_token, table_id=table_id, records=batch)
            created_records = resp_data.get("data", {}).get("records", [])
            total_written += len(created_records)
            logger.info("[write_feishu] 批次%d写入成功，本批 %d 条", i // batch_size + 1, len(created_records))
        except Exception as e:
            err = f"批次{i // batch_size + 1}写入失败: {e}"
            logger.error("[write_feishu] %s", err)
            error_messages.append(err)

    if total_written == 0 and error_messages:
        result_msg = f"写入飞书多维表格失败 (app_token={app_token}, table_id={table_id}): {'; '.join(error_messages)}"
    else:
        result_msg = f"成功写入飞书多维表格 {total_written} 条资讯记录 (app_token={app_token}, table_id={table_id})"
        if error_messages:
            result_msg += f"，部分失败: {'; '.join(error_messages)}"

    logger.info("[write_feishu] %s", result_msg)
    return WriteFeishuOutput(
        write_result=result_msg,
        app_token=app_token,
        table_id=table_id
    )
