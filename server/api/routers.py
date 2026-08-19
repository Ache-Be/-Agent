# _*_ coding : UTF-8 _*_
"""
API 路由注册入口（对标参考项目 modules.routers.register_api）
"""
from fastapi import FastAPI
from core.config import config


def register_api(app: FastAPI):
    from .v1.health import router as health_router
    from .v1.conversations import router as conv_router
    from .v1.files import router as files_router
    from .v1.chat import router as chat_router
    from .v1.config import router as config_router
    from .v1.context import router as context_router
    from .v1.reports import router as reports_router
    from .v1.students import router as students_router
    from .v1.analytics import router as analytics_router

    # 不带前缀的基础探活（前端 dashboard 直接 /healthz 调用）
    app.include_router(health_router, prefix="")

    prefix = config.api_prefix
    app.include_router(health_router, prefix=prefix)
    app.include_router(conv_router, prefix=prefix)
    app.include_router(files_router, prefix=prefix)
    app.include_router(chat_router, prefix=prefix)
    app.include_router(config_router, prefix=prefix)
    app.include_router(context_router, prefix=prefix)
    app.include_router(reports_router, prefix=prefix)
    app.include_router(students_router, prefix=prefix)
    app.include_router(analytics_router, prefix=prefix)
