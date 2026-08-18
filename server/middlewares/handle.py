# _*_ coding : UTF-8 _*_
"""
中间件注册：CORS、请求日志等
对标参考项目 middlewares.handle
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import config
from core.logging_setup import logger


def handle_middleware(app: FastAPI):
    # ---- CORS（跨域）----
    origins = config.cors_origins or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    logger.info("CORS 已配置，允许 origins：%s", origins)
