"""最终整合所需的轻量适配导出。

核心对象已经通过冻结端口直接兼容。保留这个模块是为了让未来需要包装
上下文管理或返回值的扩展有唯一入口；当前默认工厂不改变任何语义。
"""

from __future__ import annotations

from .app import MiniDBApplication, open_application


Application = MiniDBApplication
application_factory = open_application

__all__ = ["Application", "MiniDBApplication", "application_factory", "open_application"]
