import datetime
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from graphs.state import WriteFeishuInput, WriteFeishuOutput
from storage.cache.organized_news_cache import load_organized_news_cache

logger = logging.getLogger(__name__)


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


class LarkCliUnavailableError(Exception):
    pass


def _resolve_lark_cli_executable() -> str:
    candidates = ["lark-cli.cmd", "lark-cli.exe", "lark-cli"]
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise LarkCliUnavailableError("未找到 lark-cli，可执行文件不可用")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _cache_dir() -> Path:
    return _project_root() / "src" / "storage" / "cache"


def _get_base_token(state_token: str) -> str:
    return os.getenv("FEISHU_BASE_TOKEN", "").strip() or (state_token or "").strip()


def _extract_cli_json(stdout: str) -> Dict[str, Any]:
    text = (stdout or "").strip()
    if not text:
        raise ValueError("lark-cli 未返回任何输出")

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"lark-cli 输出中未找到 JSON: {text[:500]}")

    return json.loads(text[start:end + 1])


def _run_lark_cli_json(args: List[str]) -> Dict[str, Any]:
    lark_cli_executable = _resolve_lark_cli_executable()

    completed = subprocess.run(
        [lark_cli_executable, *args],
        cwd=_project_root(),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""

    if completed.returncode != 0:
        detail = stderr.strip() or stdout.strip() or f"exit_code={completed.returncode}"
        raise RuntimeError(detail)

    payload = _extract_cli_json(stdout)
    if not payload.get("ok", False):
        raise RuntimeError(json.dumps(payload, ensure_ascii=False))
    return payload


def _extract_link_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        link = value.get("link")
        return str(link).strip() if link else ""
    if isinstance(value, list):
        for item in value:
            link = _extract_link_value(item)
            if link:
                return link
        return ""

    text = str(value).strip()
    if not text:
        return ""

    match = re.search(r"\((https?://[^)]+)\)$", text)
    if match:
        return match.group(1).strip()
    return text


def _parse_accuracy(value: Any) -> float:
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


def _format_cli_datetime(date_str: Any) -> str:
    fallback_dt = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if isinstance(date_str, str):
        text = date_str.strip()
        if text:
            formats = [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%d",
            ]
            for fmt in formats:
                try:
                    dt = datetime.datetime.strptime(text, fmt)
                    if fmt == "%Y-%m-%d":
                        dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
                    return dt.strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
    return fallback_dt.strftime("%Y-%m-%d %H:%M:%S")


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


def _resolve_cli_field_name_map(base_token: str, table_id: str) -> Dict[str, str]:
    payload = _run_lark_cli_json([
        "base",
        "+field-list",
        "--as",
        "user",
        "--base-token",
        base_token,
        "--table-id",
        table_id,
    ])
    available_fields = {
        item.get("name", "")
        for item in payload.get("data", {}).get("fields", [])
        if item.get("name")
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


def _fetch_existing_cli_links(base_token: str, table_id: str, link_field_name: str) -> set[str]:
    existing_links: set[str] = set()
    offset = 0
    limit = 200

    while True:
        payload = _run_lark_cli_json([
            "base",
            "+record-list",
            "--as",
            "user",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--field-id",
            link_field_name,
            "--offset",
            str(offset),
            "--limit",
            str(limit),
            "--format",
            "json",
        ])
        data = payload.get("data", {})
        for row in data.get("data", []):
            if isinstance(row, list) and row:
                link = _extract_link_value(row[0])
                if link:
                    existing_links.add(link)

        if not data.get("has_more"):
            break
        offset += limit

    return existing_links


def _build_cli_rows(organized_news: Dict[str, Any], field_name_map: Dict[str, str]) -> List[List[Any]]:
    rows: List[List[Any]] = []
    canonical_order = [
        "领域",
        "标题",
        "行业",
        "内容摘要",
        "影响",
        "来源",
        "重要性",
        "链接",
        "发布日期",
        "预测准确率",
        "真实性评估",
    ]

    def get_item_value(item: Dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in item and item[key] not in (None, ""):
                return item[key]
        return ""

    for category, items in organized_news.items():
        if category == "raw_data" or not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, dict):
                continue

            title = get_item_value(item, "title", "标题")
            summary = get_item_value(item, "summary", "内容摘要", "摘要")
            industry = get_item_value(item, "industry", "行业", "所属行业")
            impact = get_item_value(item, "impact", "影响")
            source = get_item_value(item, "source", "来源")
            importance = get_item_value(item, "importance", "重要性") or "中"
            url = get_item_value(item, "url", "链接")
            publish_date = get_item_value(item, "publish_date", "发布日期", "time")
            accuracy_raw = get_item_value(item, "prediction_accuracy", "预测准确率")
            authenticity_raw = get_item_value(item, "authenticity", "真实性评估", "credibility")

            canonical_values: Dict[str, Any] = {
                "领域": category,
                "标题": str(title) if title else "",
                "行业": str(industry).strip() if industry else _infer_industry(category, str(title), str(summary)),
                "内容摘要": str(summary) if summary else "",
                "影响": _normalize_impact(impact, str(title), str(summary)),
                "来源": str(source) if source else "",
                "重要性": str(importance) if importance else "中",
                "链接": str(url).strip() if url else None,
                "发布日期": _format_cli_datetime(publish_date),
                "预测准确率": None if accuracy_raw in (None, "") else _parse_accuracy(accuracy_raw),
                "真实性评估": _normalize_authenticity(authenticity_raw),
            }
            rows.append([canonical_values[name] for name in canonical_order if field_name_map.get(name)])

    return rows


def _dedupe_cli_rows_by_link(rows: List[List[Any]], fields: List[str], existing_links: set[str]) -> List[List[Any]]:
    if "链接" not in fields:
        return rows

    link_index = fields.index("链接")
    deduped_rows: List[List[Any]] = []
    seen_links = set(existing_links)

    for row in rows:
        link = _extract_link_value(row[link_index] if link_index < len(row) else None)
        if link and link in seen_links:
            continue
        if link:
            seen_links.add(link)
        deduped_rows.append(row)

    return deduped_rows


def _write_records_with_lark_cli(base_token: str, table_id: str, organized_news: Dict[str, Any]) -> int:
    field_name_map = _resolve_cli_field_name_map(base_token, table_id)
    canonical_order = [
        "领域",
        "标题",
        "行业",
        "内容摘要",
        "影响",
        "来源",
        "重要性",
        "链接",
        "发布日期",
        "预测准确率",
        "真实性评估",
    ]
    fields = [field_name_map[name] for name in canonical_order if field_name_map.get(name)]
    rows = _build_cli_rows(organized_news, field_name_map)

    link_field_name = field_name_map.get("链接")
    if link_field_name:
        existing_links = _fetch_existing_cli_links(base_token, table_id, link_field_name)
        deduped_rows = _dedupe_cli_rows_by_link(rows, fields, existing_links)
        logger.info(
            "[write_feishu] 写入前按链接去重: 原始 %d 条, 去重后 %d 条, 已存在链接 %d 条",
            len(rows),
            len(deduped_rows),
            len(existing_links),
        )
        rows = deduped_rows

    if not rows:
        return 0

    cache_dir = _cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)

    batch_size = 200
    total_written = 0
    for batch_index, start in enumerate(range(0, len(rows), batch_size), start=1):
        batch_rows = rows[start:start + batch_size]
        payload_path = cache_dir / f"records.batch-{batch_index:03d}.json"
        payload_path.write_text(
            json.dumps({"fields": fields, "rows": batch_rows}, ensure_ascii=False),
            encoding="utf-8",
        )
        relative_payload_path = f"@./{payload_path.relative_to(_project_root()).as_posix()}"
        payload = _run_lark_cli_json([
            "base",
            "+record-batch-create",
            "--as",
            "user",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--json",
            relative_payload_path,
        ])
        created_rows = payload.get("data", {}).get("data", [])
        total_written += len(created_rows) if isinstance(created_rows, list) else len(batch_rows)
        logger.info("[write_feishu] lark-cli 批次%d写入成功，本批 %d 条", batch_index, len(batch_rows))

    return total_written


def write_feishu_node(
    state: WriteFeishuInput, config: RunnableConfig, runtime: Runtime[Any]
) -> WriteFeishuOutput:
    """
    title: 写入飞书多维表格
    desc: 使用 lark-cli(user) 从缓存或当前状态读取结构化资讯，按链接去重后批量写入飞书多维表格
    integrations: 飞书多维表格
    """
    base_token = _get_base_token(state.base_token)
    table_id = (state.table_id or "").strip()
    organized_news = state.organized_news

    if not organized_news:
        try:
            organized_news = load_organized_news_cache()
            if organized_news:
                logger.info("[write_feishu] 当前输入无 organized_news，已从缓存加载待写入数据")
        except Exception as e:
            logger.warning("[write_feishu] 读取清洗结果缓存失败: %s", e)

    if not base_token:
        error_msg = "缺少可用的 base_token，请提供 FEISHU_BASE_TOKEN 或在输入中传入可复用的 token"
        logger.error("[write_feishu] %s", error_msg)
        return WriteFeishuOutput(write_result=error_msg, base_token="", table_id=table_id)

    if not table_id:
        error_msg = "缺少必填参数 table_id；CLI 写入路径不再负责自动建表"
        logger.error("[write_feishu] %s", error_msg)
        return WriteFeishuOutput(write_result=error_msg, base_token=base_token, table_id="")

    try:
        total_written = _write_records_with_lark_cli(base_token, table_id, organized_news)
    except LarkCliUnavailableError as e:
        error_msg = f"lark-cli 不可用，无法写入飞书: {e}"
        logger.error("[write_feishu] %s", error_msg)
        return WriteFeishuOutput(write_result=error_msg, base_token=base_token, table_id=table_id)
    except Exception as e:
        error_msg = f"通过 lark-cli(user) 写入飞书失败: {e}"
        logger.error("[write_feishu] %s", error_msg)
        return WriteFeishuOutput(write_result=error_msg, base_token=base_token, table_id=table_id)

    if total_written == 0:
        result_msg = "本次无新资讯需写入飞书多维表格"
    else:
        result_msg = f"成功通过 lark-cli(user) 写入飞书多维表格 {total_written} 条资讯记录 (base_token={base_token}, table_id={table_id})"

    logger.info("[write_feishu] %s", result_msg)
    return WriteFeishuOutput(write_result=result_msg, base_token=base_token, table_id=table_id)
