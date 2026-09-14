"""确定性的 Token 和 AST JSON 输出；显式栈支持长布尔表达式树。"""

from dataclasses import fields
from collections.abc import Mapping
import json

from minidb.contracts.ast import (
    BinaryExpr, ColumnDef, CreateTableStmt, DeleteStmt, Identifier,
    InsertStmt, Literal, SelectStmt, UnaryExpr,
)
from minidb.contracts.source import Span
from minidb.contracts.extensions import ExtensionStatement
from minidb.contracts.tokens import Token


AST_TYPES = (Literal, Identifier, UnaryExpr, BinaryExpr, ColumnDef,
             CreateTableStmt, InsertStmt, SelectStmt, DeleteStmt, ExtensionStatement)


def _span_data(span: Span) -> dict:
    # 将位置对象展开成 JSON 可保存的字段，保留起止偏移量及行列号。
    return {name: {"offset": p.offset, "line": p.line, "column": p.column}
            for name, p in (("start", span.start), ("end", span.end))}


def ast_to_data(node: object) -> object:
    """把节点或语句序列转换成 {kind,fields,span}，不重新解析 SQL。"""
    result = [None]
    # 待处理项为“当前值、目标容器、写入位置”，用栈代替深层递归调用。
    pending = [(node, result, 0)]
    while pending:
        value, parent, key = pending.pop()
        if isinstance(value, AST_TYPES):
            # 每个节点统一输出类型、业务字段和源码位置，便于查看与跨模块传递。
            record = {"kind": type(value).__name__, "fields": {}, "span": _span_data(value.span)}
            parent[key] = record
            members = [field.name for field in fields(value) if field.name != "span"]
            for name in members:
                record["fields"][name] = None
            # 栈后进先出，逆序压入才能按字段的声明顺序处理。
            for name in reversed(members):
                pending.append((getattr(value, name), record["fields"], name))
        elif isinstance(value, Mapping):
            target = {name: None for name in value}
            parent[key] = target
            for name, child in reversed(list(value.items())):
                pending.append((child, target, name))
        elif isinstance(value, (list, tuple)):
            # JSON 没有 tuple，把 AST 内的不可变序列转换成新的列表。
            items = [None] * len(value)
            parent[key] = items
            for index in reversed(range(len(value))):
                pending.append((value[index], items, index))
        elif value is None or type(value) in (str, int, float, bool):
            parent[key] = value
        else:
            raise TypeError(f"不支持的 AST 字段类型：{type(value).__name__}")
    return result[0]


def _integer_text(value: int) -> str:
    if value.bit_length() < 13000:
        return str(value)
    negative = value < 0
    value = abs(value)
    chunks = []
    while value:
        # 大整数按十亿进制拆块，输出时除最高块外都补足九位。
        value, remainder = divmod(value, 1_000_000_000)
        chunks.append(remainder)
    body = str(chunks.pop()) + "".join(f"{chunk:09d}" for chunk in reversed(chunks))
    return ("-" if negative else "") + body


def _json_text(data: object) -> str:
    # json.dumps 对深层 dict 递归；这里只用它编码标量，容器采用显式栈。
    output = []
    stack = [(False, data)]
    while stack:
        raw, value = stack.pop()
        if raw:
            # raw 表示已经准备好的 JSON 标点，直接拼接，不再加引号。
            output.append(value)
        elif isinstance(value, dict):
            output.append("{")
            stack.append((True, "}"))
            pairs = list(value.items())
            for index in reversed(range(len(pairs))):
                key, child = pairs[index]
                stack.append((False, child))
                stack.append((True, ": "))
                stack.append((False, key))
                if index:
                    stack.append((True, ", "))
        elif isinstance(value, list):
            output.append("[")
            stack.append((True, "]"))
            for index in reversed(range(len(value))):
                stack.append((False, value[index]))
                if index:
                    stack.append((True, ", "))
        elif type(value) is int:
            output.append(_integer_text(value))
        else:
            # 交给标准库处理字符串转义；中文保留原字符，拒绝 NaN/Infinity。
            output.append(json.dumps(value, ensure_ascii=False, allow_nan=False))
    return "".join(output)


def format_ast(node: object) -> str:
    # 先转成普通数据，再输出稳定文本；不会重新解析 SQL 或修改原 AST。
    return _json_text(ast_to_data(node))


def format_tokens(tokens: list[Token] | tuple[Token, ...]) -> str:
    # 同时展示种别、原词素、解释后的值和位置，便于对照词法分析过程。
    return _json_text([
        {"kind": t.kind.name, "lexeme": t.lexeme, "value": t.value, "span": _span_data(t.span)}
        for t in tokens
    ])
