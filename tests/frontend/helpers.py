"""测试中的独立坐标计算和 AST 结构摘要，不复用生产算法。"""

from minidb.contracts.ast import BinaryExpr, Identifier, Literal, UnaryExpr
from minidb.contracts.source import Position
from minidb.frontend.parser import Frontend


def parse_one(source):
    statements = Frontend().parse(source)
    assert len(statements) == 1
    return statements[0]


def shape(expr):
    if isinstance(expr, Identifier):
        return expr.name
    if isinstance(expr, Literal):
        return expr.value
    if isinstance(expr, UnaryExpr):
        return (expr.op, shape(expr.operand))
    if isinstance(expr, BinaryExpr):
        return (expr.op, shape(expr.left), shape(expr.right))
    raise AssertionError(type(expr))


def position_at(source, offset):
    prefix = source[:offset].replace("\r\n", "\n").replace("\r", "\n")
    pieces = prefix.split("\n")
    return Position(offset, len(pieces), len(pieces[-1]) + 1)
