"""ExecutionResult 与领域错误的纯文本格式化。"""

from __future__ import annotations

import json

from minidb.contracts import ExecutionResult, MiniDBError


def _escape_cell(value: int | str) -> str:
    if isinstance(value, int):
        return str(value)
    return value.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n").replace("|", "\\|")


def _format_table(result: ExecutionResult) -> str:
    lines: list[str] = []
    if result.columns:
        lines.append("| " + " | ".join(result.columns) + " |")
        lines.append("| " + " | ".join("---" for _ in result.columns) + " |")
        lines.extend("| " + " | ".join(_escape_cell(value) for value in row) + " |" for row in result.rows)
        lines.append(f"返回 {len(result.rows)} 行")
    else:
        if result.message:
            lines.append(result.message)
        lines.append(f"受影响行数: {result.affected_rows}")
    if result.message and result.columns:
        lines.append(result.message)
    if result.trace:
        lines.append("TRACE")
        lines.extend(f"[{event.stage}] {event.detail}" for event in result.trace)
    return "\n".join(lines)


def _format_json(result: ExecutionResult) -> str:
    payload = {
        "columns": list(result.columns),
        "rows": [list(row) for row in result.rows],
        "affected_rows": result.affected_rows,
        "message": result.message,
        "trace": [{"stage": event.stage, "detail": event.detail} for event in result.trace],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def format_result(result: ExecutionResult, output_format: str = "table") -> str:
    """将不可变结果转换为 table 或 JSON 文本，不修改原对象。"""

    normalized = output_format.casefold()
    if normalized == "table":
        return _format_table(result)
    if normalized == "json":
        return _format_json(result)
    raise ValueError(f"未知结果格式 {output_format!r}")


def format_error(error: MiniDBError) -> str:
    """保留领域错误的阶段、代码、位置与原因。"""

    return str(error)


__all__ = ["format_error", "format_result"]
