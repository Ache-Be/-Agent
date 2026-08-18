# _*_ coding : UTF-8 _*_
"""
分析上下文：供前端对话页面预加载（显示当前分析范围）
"""
from fastapi import APIRouter
from core import state
from models.schemas import ContextResponse

router = APIRouter(tags=["分析上下文"])


@router.get("/context", response_model=ContextResponse)
async def api_context():
    if not state.latest_agg_data:
        return ContextResponse(has_data=False)
    ad = state.latest_agg_data
    return ContextResponse(
        has_data=True,
        total_students=ad.get("total_students", 0),
        weak_count=ad.get("weak_student_count", 0),
        experiment_count=ad.get("experiment_count", 0),
        quiz_count=ad.get("quiz_count", 0),
        unit_count=ad.get("unit_count", 0),
        attendance_count=ad.get("attendance_count", 0),
        file_count=len(state.latest_results),
    )
