"""
clean_and_update_table.py
读取飞书多维表格 tblGd2XrhSQS54Sn 全量记录，
对六类不完整字段进行清洗，并批量更新回表（不新增记录）。

六类清洗规则：
1. 行业为空              → _infer_industry() 推断
2. 影响非"好"/"坏"       → _normalize_impact() 修正
3. 内容摘要为空          → 保留为空（无法补充，跳过）
4. 真实性评估不在{高,中,低} → _normalize_authenticity() 修正，默认"中"
5. 预测准确率非法        → _parse_accuracy() 规范化为 0.0~1.0
6. 发布日期为空/非法     → 用当前时间填充
"""
import sys
import os
import json
import datetime
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── 路径 & 环境 ───────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

for line in (PROJECT_ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

from tools.feishu_bitable import FeishuBitable  # noqa: E402
from graphs.nodes.write_feishu_node import (    # noqa: E402
    _infer_industry,
    _normalize_impact,
    _normalize_authenticity,
    _parse_date_to_timestamp_ms,
    _parse_accuracy,
)

APP_TOKEN = os.environ["FEISHU_APP_TOKEN"]
TABLE_ID  = os.environ["FEISHU_TABLE_ID"]

VALID_AUTHENTICITY = {"高", "中", "低"}
VALID_IMPACT       = {"好", "坏"}
VALID_DOMAIN       = {"科技股", "港股基金021378持仓", "大宗商品", "市场震荡"}

# 飞书表实际字段名映射（与 write_feishu_node.py 的逻辑字段名可能不同）
FIELD_DATE = "时间"          # 日期字段在表里叫"时间"


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _get_text(v: Any) -> str:
    """从飞书 API 返回值中提取纯文本"""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        # 文本字段有时返回 [{type:"text", text:"..."}]
        parts = []
        for seg in v:
            if isinstance(seg, dict):
                parts.append(seg.get("text", ""))
            elif isinstance(seg, str):
                parts.append(seg)
        return "".join(parts).strip()
    return ""


def _get_link(v: Any) -> str:
    """从超链接字段提取 URL"""
    if isinstance(v, dict):
        return v.get("link", "") or v.get("url", "") or ""
    if isinstance(v, str):
        return v
    return ""


def _get_select(v: Any) -> str:
    """从单选字段提取文字"""
    if isinstance(v, dict):
        return v.get("value", "") or v.get("text", "") or ""
    if isinstance(v, str):
        return v.strip()
    return ""


def _get_number(v: Any) -> Optional[float]:
    """从数字字段提取 float"""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace("%", ""))
        except ValueError:
            return None
    return None


def _get_date_ms(v: Any) -> Optional[int]:
    """从飞书日期字段提取毫秒时间戳（原始值就是 ms int）"""
    if isinstance(v, (int, float)) and v > 0:
        return int(v)
    return None


# ── 主逻辑 ────────────────────────────────────────────────────────────────────

def list_all_records(bitable: FeishuBitable) -> List[Dict[str, Any]]:
    """分页拉取全量记录"""
    all_records: List[Dict[str, Any]] = []
    page_token: Optional[str] = None
    page = 0
    while True:
        page += 1
        resp = bitable.list_records(
            app_token=APP_TOKEN,
            table_id=TABLE_ID,
            page_size=500,
            page_token=page_token,
        )
        data = resp.get("data", {})
        items = data.get("items", [])
        all_records.extend(items)
        logger.info("第 %d 页，获取 %d 条，累计 %d 条", page, len(items), len(all_records))
        if not data.get("has_more") or not items:
            break
        page_token = data.get("page_token")
    return all_records


def needs_cleaning(fields: Dict[str, Any]) -> bool:
    """检查该记录是否有任何脏字段"""
    industry = _get_text(fields.get("行业"))
    impact   = _get_select(fields.get("影响"))
    auth     = _get_select(fields.get("真实性评估"))
    acc_raw  = _get_number(fields.get("预测准确率"))
    date_ms  = _get_date_ms(fields.get(FIELD_DATE))

    if not industry:
        return True
    if impact not in VALID_IMPACT:
        return True
    if auth not in VALID_AUTHENTICITY:
        return True
    if acc_raw is None or not (0.0 <= acc_raw <= 1.0):
        return True
    if date_ms is None:
        return True
    return False


def build_patch(record_id: str, fields: Dict[str, Any], now_ms: int) -> Optional[Dict[str, Any]]:
    """
    对单条记录生成清洗 patch（仅包含需要修改的字段）。
    返回 None 表示无需修改。
    """
    domain   = _get_select(fields.get("领域"))
    title    = _get_text(fields.get("标题"))
    summary  = _get_text(fields.get("内容摘要"))
    industry = _get_text(fields.get("行业"))
    impact   = _get_select(fields.get("影响"))
    auth_raw = fields.get("真实性评估")
    auth     = _get_select(auth_raw)
    acc_raw  = _get_number(fields.get("预测准确率"))
    date_ms  = _get_date_ms(fields.get(FIELD_DATE))

    patch: Dict[str, Any] = {}

    # 1. 行业为空
    if not industry:
        patch["行业"] = _infer_industry(domain, title, summary)

    # 2. 影响非法
    if impact not in VALID_IMPACT:
        patch["影响"] = _normalize_impact(impact, title, summary)

    # 3. 真实性评估非法
    if auth not in VALID_AUTHENTICITY:
        norm = _normalize_authenticity(auth_raw)
        patch["真实性评估"] = norm

    # 4. 预测准确率非法
    if acc_raw is None or not (0.0 <= acc_raw <= 1.0):
        if acc_raw is not None:
            # 可能是百分比形式（如 75 表示 75%）
            fixed = acc_raw / 100.0 if acc_raw > 1.0 else acc_raw
            if 0.0 <= fixed <= 1.0:
                patch["预测准确率"] = fixed
            else:
                patch["预测准确率"] = 0.0
        else:
            patch["预测准确率"] = 0.0

    # 5. 时间字段为空
    if date_ms is None:
        patch[FIELD_DATE] = now_ms

    if not patch:
        return None
    return {"record_id": record_id, "fields": patch}


def batch_update(bitable: FeishuBitable, patches: List[Dict[str, Any]]) -> Tuple[int, int]:
    """分批调用 update_records，返回 (成功条数, 失败条数)"""
    ok_count = 0
    fail_count = 0
    batch_size = 500
    for i in range(0, len(patches), batch_size):
        batch = patches[i : i + batch_size]
        try:
            resp = bitable.update_records(
                app_token=APP_TOKEN,
                table_id=TABLE_ID,
                records=batch,
            )
            updated = resp.get("data", {}).get("records", [])
            ok_count += len(updated)
            logger.info("批次 %d：更新 %d 条成功", i // batch_size + 1, len(updated))
        except Exception as e:
            fail_count += len(batch)
            logger.error("批次 %d：更新失败 %s", i // batch_size + 1, e)
    return ok_count, fail_count


def main():
    logger.info("=== 飞书多维表格数据清洗脚本启动 ===")
    logger.info("APP_TOKEN=%s  TABLE_ID=%s", APP_TOKEN, TABLE_ID)

    bitable = FeishuBitable()
    today = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    now_ms = int(today.timestamp() * 1000)

    # Step 1: 拉取全量记录
    logger.info("Step1: 拉取全量记录 …")
    records = list_all_records(bitable)
    logger.info("共获取 %d 条记录", len(records))

    # Step 2: 检测脏数据并生成 patch
    logger.info("Step2: 检测脏字段并生成清洗 patch …")
    patches: List[Dict[str, Any]] = []
    dirty_count = 0
    for rec in records:
        record_id = rec.get("record_id", "")
        fields = rec.get("fields", {})
        if needs_cleaning(fields):
            dirty_count += 1
            patch = build_patch(record_id, fields, now_ms)
            if patch:
                patches.append(patch)

    logger.info("共发现 %d 条脏记录，生成 %d 个 patch", dirty_count, len(patches))

    if not patches:
        logger.info("无需更新，退出")
        return

    # 打印示例 patch
    logger.info("示例 patch（前3条）: %s", json.dumps(patches[:3], ensure_ascii=False, indent=2))

    # Step 3: 批量更新
    logger.info("Step3: 批量更新 …")
    ok, fail = batch_update(bitable, patches)

    logger.info("=== 完成 ===  成功更新 %d 条，失败 %d 条", ok, fail)


if __name__ == "__main__":
    main()
