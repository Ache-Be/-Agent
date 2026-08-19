# _*_ coding : UTF-8 _*_
"""
新架构 · 数据聚合 API：
  - /analytics/overview        仪表盘大盘（文件数/学生数/班级数/实验数/平均分/薄弱率）
  - /analytics/student_summary 学生聚合视图（班级×学生 维度）
  - /analytics/class_summary   班级×实验聚合视图
  - /analytics/hybrid_search   结构化 + 向量相似度混合检索（RAG 前置调试接口）
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from core.config import config
from core.logging_setup import logger
from services.embedding import embedding

router = APIRouter(prefix="/analytics", tags=["新架构·数据聚合"])


def _pg():
    from core.db import pg_store
    return pg_store


@router.get("/overview", summary="仪表盘大盘统计")
def overview() -> Dict[str, Any]:
    try:
        stats = _pg().overview_stats()
        return {"ok": True, "data": stats}
    except Exception as e:
        logger.exception("overview 异常: %s", e)
        # 如果 pgvector 还没初始化，兜底返回 state 层的旧聚合，保证前端不崩
        try:
            from core import state
            agg = state.latest_agg_data or {}
            return {
                "ok": True,
                "data": {
                    "file_count": len(state.file_hashes or {}),
                    "student_count": agg.get("total_students", 0),
                    "class_count": 0,
                    "experiment_count": agg.get("experiment_count", 0),
                    "avg_score": agg.get("avg_score", 0.0),
                    "weak_rate_percent": agg.get("weak_rate", 0.0),
                    "fallback_to_state": True,
                },
            }
        except Exception as e2:
            raise HTTPException(status_code=500, detail=f"overview 查询失败：{e}（fallback也失败：{e2}）")


@router.get("/student_summary", summary="学生维度聚合视图")
def student_summary(
    class_name: Optional[str] = Query(None, description="班级名（精确匹配，来自 relative_path 倒数第二层）"),
    student_id: Optional[str] = Query(None, description="学号精确匹配"),
    name: Optional[str] = Query(None, description="姓名模糊匹配"),
    min_weak_rate: Optional[float] = Query(None, ge=0, le=100, description="薄弱率下限(%)"),
    max_avg_score: Optional[float] = Query(None, ge=0, le=100, description="平均分上限"),
    sort_by: str = Query("avg_score", description="排序字段: avg_score / weak_rate_percent / experiment_count / weak_count"),
    sort_desc: bool = Query(True, description="是否降序"),
    limit: int = Query(500, ge=1, le=2000),
) -> Dict[str, Any]:
    try:
        rows = _pg().student_summary(
            class_name=class_name, student_id=student_id, name=name,
            min_weak_rate=min_weak_rate, max_avg_score=max_avg_score,
            limit=limit, sort_by=sort_by, sort_desc=sort_desc,
        )
        return {"ok": True, "total": len(rows), "data": rows}
    except Exception as e:
        logger.exception("student_summary 异常: %s", e)
        raise HTTPException(status_code=500, detail=f"student_summary 查询失败：{e}")


@router.get("/class_summary", summary="班级×实验聚合视图")
def class_summary(
    class_name: Optional[str] = Query(None, description="班级名（模糊 LIKE）"),
    experiment_name: Optional[str] = Query(None, description="实验名（模糊 LIKE）"),
    source_type: Optional[str] = Query(None, description="touge/mooc/quiz/unit/attendance"),
    limit: int = Query(500, ge=1, le=2000),
) -> Dict[str, Any]:
    try:
        rows = _pg().class_summary(
            class_name=class_name, experiment_name=experiment_name,
            source_type=source_type, limit=limit,
        )
        return {"ok": True, "total": len(rows), "data": rows}
    except Exception as e:
        logger.exception("class_summary 异常: %s", e)
        raise HTTPException(status_code=500, detail=f"class_summary 查询失败：{e}")


@router.post("/hybrid_search", summary="RAG 混合检索（调试用）")
def hybrid_search(
    body: Dict[str, Any],
) -> Dict[str, Any]:
    """
    body:
      {
        "query": "学生张三的薄弱知识点",      # 必填，自然语言提问
        "top_k": 20,                          # 选填，默认 20
        "student_id": null, "name": null, "class_name": null,
        "experiment_name": null, "source_type": null,
        "min_score": null, "max_score": null,
        "vector_only": false
      }
    """
    try:
        q = (body or {}).get("query")
        if not q or not isinstance(q, str) or not q.strip():
            raise HTTPException(status_code=422, detail="query 必填，且不能是空字符串")
        top_k = int(body.get("top_k", 20))
        # 显式过滤参数优先；没传过滤条件时自动抽取意图，与 chat RAG 链路行为一致
        has_explicit = any(body.get(k) is not None for k in (
            "student_id", "name", "class_name", "experiment_name",
            "source_type", "min_score", "max_score", "vector_only",
        ))
        if not has_explicit:
            from services.rag_service import _extract_intent
            intent = _extract_intent(q)
            body = {**body, **{k: intent.get(k) for k in (
                "student_id", "name", "class_name", "experiment_name",
                "min_score", "max_score", "vector_only",
            )}}
        q_vec = embedding.encode_one(q)
        rows = _pg().hybrid_search(
            q_vec,
            student_id=body.get("student_id"),
            name=body.get("name"),
            class_name=body.get("class_name"),
            experiment_name=body.get("experiment_name"),
            source_type=body.get("source_type"),
            min_score=body.get("min_score"),
            max_score=body.get("max_score"),
            top_k=top_k,
            vector_only=bool(body.get("vector_only", False)),
        )
        return {"ok": True, "total": len(rows), "data": rows}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("hybrid_search 异常: %s", e)
        raise HTTPException(status_code=500, detail=f"hybrid_search 失败：{e}")
