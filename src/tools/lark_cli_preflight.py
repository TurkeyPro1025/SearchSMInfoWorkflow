import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional


class LarkCliPreflightError(RuntimeError):
    pass


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_lark_cli_executable() -> str:
    candidates = ["lark-cli.cmd", "lark-cli.exe", "lark-cli"]
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise LarkCliPreflightError("CLI 预检失败：未找到 lark-cli，可先安装并完成脚手架初始化")


def _extract_cli_json(output: str) -> Optional[Dict[str, Any]]:
    text = (output or "").strip()
    if not text:
        return None

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None

    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _summarize_cli_failure(command_name: str, stdout: str, stderr: str, returncode: int) -> str:
    payload = _extract_cli_json(stderr) or _extract_cli_json(stdout)
    if payload:
        error = payload.get("error") or {}
        message = error.get("message") or payload.get("message") or json.dumps(payload, ensure_ascii=False)
        hint = error.get("hint")
        if hint:
            return f"CLI 预检失败：{command_name} 未通过，{message}；建议：{hint}"
        return f"CLI 预检失败：{command_name} 未通过，{message}"

    detail = (stderr or stdout or "").strip()
    if not detail:
        detail = f"exit_code={returncode}"
    return f"CLI 预检失败：{command_name} 未通过，{detail}"


def _run_cli_check(args: list[str], command_name: str) -> None:
    lark_cli_executable = _resolve_lark_cli_executable()
    completed = subprocess.run(
        [lark_cli_executable, *args],
        cwd=_project_root(),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise LarkCliPreflightError(
            _summarize_cli_failure(command_name, completed.stdout or "", completed.stderr or "", completed.returncode)
        )

    payload = _extract_cli_json(completed.stdout or "")
    if payload is not None and (payload.get("ok") is False or payload.get("error")):
        raise LarkCliPreflightError(
            _summarize_cli_failure(command_name, completed.stdout or "", completed.stderr or "", completed.returncode)
        )


def ensure_lark_cli_preflight(base_token: Optional[str], table_id: Optional[str]) -> None:
    resolved_base_token = (base_token or os.getenv("FEISHU_BASE_TOKEN") or "").strip()
    resolved_table_id = (table_id or os.getenv("FEISHU_TABLE_ID") or "").strip()

    if not resolved_base_token or not resolved_table_id:
        raise LarkCliPreflightError("CLI 预检失败：缺少 base_token 或 table_id，无法先做飞书权限校验")

    _run_cli_check(["auth", "status"], "lark-cli auth status")
    _run_cli_check(
        [
            "base",
            "+field-list",
            "--as",
            "user",
            "--base-token",
            resolved_base_token,
            "--table-id",
            resolved_table_id,
        ],
        "lark-cli base +field-list",
    )