# _*_ coding : UTF-8 _*_
"""
教学预警系统 FastAPI 主入口
对标参考项目 server/app.py
"""
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

_BASE_DIR = Path(__file__).parent.resolve()
_PROJECT_ROOT = _BASE_DIR.parent
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
try:
    os.chdir(_BASE_DIR)
except OSError:
    pass

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from core.config import config
from core.logging_setup import logger
from core import state
from core.utils import backup_knowledge_base, trim_report_archives
from middlewares.handle import handle_middleware
from exceptions.handle import handle_exception
from api.routers import register_api
from services.analysis_service import restore_uploads_on_startup


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动 / 关闭生命周期"""
    logger.info("=" * 60)
    logger.info("%s 开始启动 ...", config.app().get("name", "Teaching Warning API"))
    # 1. 加载知识库
    state.load_knowledge_once()
    # 2. 恢复已上传的分析文件（优先快照，否则重新解析）
    try:
        restore_uploads_on_startup()
    except Exception as e:
        logger.warning("启动恢复上传数据失败（不影响服务运行）: %s", e, exc_info=True)
    # 3. 报告归档治理 + 知识库备份
    try:
        trim_report_archives()
        backup_knowledge_base()
    except Exception as e:
        logger.warning("归档/备份操作失败: %s", e)

    logger.info("%s 启动成功 ✅", config.app().get("name"))
    logger.info("API 文档地址:  http://localhost:%d/docs", config.port)
    yield
    logger.info("服务关闭")


docs_enabled = bool(config.get("app.api_status_enabled", True))

app = FastAPI(
    title=config.app().get("name", "教学预警系统 API"),
    description="教学预警系统后端接口文档（FastAPI + Vue 3）",
    version=config.app().get("version", "1.0.0"),
    lifespan=lifespan,
    openapi_url=f"{config.api_prefix}/openapi.json" if docs_enabled else None,
    docs_url="/docs" if docs_enabled else None,
    redoc_url="/redoc" if docs_enabled else None,
)

handle_middleware(app)
handle_exception(app)
register_api(app)

# 挂载前端打包产物（如果存在）
dist_dir = (config.BASE_DIR / ".." / "frontend" / "dist").resolve()
if dist_dir.exists() and (dist_dir / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="frontend")
    logger.info("检测到前端构建产物：%s（已挂载到 /）", dist_dir)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app="app:app",
        host=config.host,
        port=config.port,
        reload=bool(config.get("app.reload", True)),
    )
