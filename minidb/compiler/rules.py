"""可注册的不可变计划优化规则框架。"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from minidb.contracts.errors import OptimizationError
from minidb.contracts.plans import PlanNode

from .plan_formatter import format_plan


PlanTransform: type = Callable[[PlanNode], PlanNode]


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    priority: int
    apply: PlanTransform

    def __post_init__(self) -> None:
        if not self.name or self.name.strip() != self.name:
            raise ValueError("规则名必须是非空稳定文本")
        if not callable(self.apply):
            raise TypeError("规则 apply 必须可调用")


@dataclass(frozen=True, slots=True)
class RuleTraceEntry:
    rule: str
    round_number: int
    before: str
    after: str
    changes: int


@dataclass(slots=True)
class RuleTrace:
    records: list[RuleTraceEntry] = field(default_factory=list)

    def record(self, rule: Rule, round_number: int, before: PlanNode, after: PlanNode) -> None:
        if before == after:
            return
        self.records.append(
            RuleTraceEntry(rule.name, round_number, format_plan(before), format_plan(after), 1)
        )


class RuleOptimizer:
    """按稳定顺序重复运行规则，直到结构固定或达到轮次上限。"""

    def __init__(
        self,
        rules: Iterable[Rule] = (),
        *,
        max_rounds: int = 8,
        enabled_names: Iterable[str] | None = None,
    ) -> None:
        if max_rounds < 1:
            raise ValueError("max_rounds 必须大于或等于 1")
        self.max_rounds = max_rounds
        self._rules: dict[str, Rule] = {}
        self.trace = RuleTrace()
        self.enabled_names = None if enabled_names is None else frozenset(enabled_names)
        for rule in rules:
            self.register(rule)

    @property
    def rules(self) -> tuple[Rule, ...]:
        return tuple(sorted(self._rules.values(), key=lambda rule: (rule.priority, rule.name)))

    def register(self, rule: Rule) -> None:
        if rule.name in self._rules:
            raise ValueError(f"重复规则名 {rule.name!r}")
        self._rules[rule.name] = rule

    def _selected_rules(self, enabled_names: Iterable[str] | None) -> tuple[Rule, ...]:
        selected_names = self.enabled_names if enabled_names is None else frozenset(enabled_names)
        if selected_names is not None:
            unknown = sorted(selected_names - self._rules.keys())
            if unknown:
                raise ValueError(f"未知规则名 {unknown!r}")
        return tuple(rule for rule in self.rules if selected_names is None or rule.name in selected_names)

    def run(self, plan: PlanNode, enabled_names: Iterable[str] | None = None) -> PlanNode:
        selected = self._selected_rules(enabled_names)
        current = plan
        if not selected:
            return current
        for round_number in range(1, self.max_rounds + 1):
            round_start = current
            for rule in selected:
                before = current
                current = rule.apply(current)
                self.trace.record(rule, round_number, before, current)
            if current == round_start:
                return current
        last_rule = selected[-1].name
        raise OptimizationError(
            "ITERATION_LIMIT",
            f"优化规则未在 {self.max_rounds} 轮内达到固定点",
            context={"rule": last_rule, "max_rounds": self.max_rounds},
        )


__all__ = ["Rule", "RuleOptimizer", "RuleTrace", "RuleTraceEntry"]
