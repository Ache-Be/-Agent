# _*_ coding : UTF-8 _*_
"""
健康检查 & 基础探活
"""
from datetime import datetime
from fastapi import APIRouter

from core import state
from core.utils import list_conversations
from models.schemas import HealthzResponse

router = APIRouter(tags=["健康检查"])


@router.get("/healthz", response_model=HealthzResponse)
async def healthz():
    """前端顶栏 + 仪表盘 探活：文件数、会话数、是否有分析数据、当前时间"""
    files_count = 0
    try:
        from services.analysis_service import list_data_files
        files_count = len(list_data_files())
    except Exception:
        files_count = 0
    conv_count = len(list_conversations())
    has_analysis = bool(state.latest_agg_data) or files_count > 0
    return HealthzResponse(
        status="ok",
        time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        files=files_count,
        has_analysis=has_analysis,
        conversations=conv_count,
    )
