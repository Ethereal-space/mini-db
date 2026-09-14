"""成员 A 所需的只读诊断事件。执行结果模型留待共同基线统一补齐。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TraceEvent:
    stage: str
    detail: str
