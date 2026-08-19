# _*_ coding : UTF-8 _*_
"""
系统配置：DeepSeek API Key + 分析阈值（薄弱率/低分线/查答案率）
保存阈值后：
  1) 清 analysis.config 模块级缓存，保证重新分析用到新值
  2) 调用 rebuild_agg_data 重新跑学生聚合（阈值改变会改变薄弱学生判断、画像等级）
  3) AI 教学助手对话立刻读最新聚合数据
"""
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from core.logging_setup import logger
from core.utils import (
    load_api_key,
    save_api_key,
    save_analysis_thresholds,
    load_analysis_thresholds,
    load_chat_flags,
    save_chat_flags,
)

router = APIRouter(tags=["系统配置"])


class FullConfigRequest(BaseModel):
    api_key: Optional[str] = None
    weak_threshold: Optional[float] = Field(default=None, ge=0.3, le=0.95, description="薄弱得分率阈值(小数)")
    low_score_line: Optional[float] = Field(default=None, ge=20, le=90, description="低分线(百分制整数)")
    view_answer_alert_rate: Optional[float] = Field(default=None, ge=0.01, le=0.8, description="查答案率关注线(小数)")
    enable_qa_sediment_ref: Optional[bool] = Field(default=None, description="AI 回答时是否参考历史问答沉淀")


class FullConfigResponse(BaseModel):
    api_configured: bool
    weak_threshold: float
    low_score_line: float
    view_answer_alert_rate: float
    enable_qa_sediment_ref: bool


@router.get("/config", response_model=FullConfigResponse)
async def api_get_config():
    key = load_api_key()
    th = load_analysis_thresholds()
    flags = load_chat_flags()
    return FullConfigResponse(
        api_configured=bool(key),
        weak_threshold=float(th["weak_threshold"]),
        low_score_line=float(th["low_score_line"]),
        view_answer_alert_rate=float(th["view_answer_alert_rate"]),
        enable_qa_sediment_ref=bool(flags.get("enable_qa_sediment_ref", True)),
    )


@router.post("/config")
async def api_set_config(body: FullConfigRequest):
    msgs = []
    # 1. API Key
    if body.api_key is not None:
        k = body.api_key.strip()
        if not k:
            return {"ok": False, "msg": "API Key 不能为空"}
        save_api_key(k)
        msgs.append("DeepSeek API Key 已保存")

    # 2. 阈值
    thresholds_changed = False
    current_th = load_analysis_thresholds()
    new_th = dict(current_th)
    if body.weak_threshold is not None:
        new_th["weak_threshold"] = float(body.weak_threshold)
        thresholds_changed = True
    if body.low_score_line is not None:
        new_th["low_score_line"] = float(body.low_score_line)
        thresholds_changed = True
    if body.view_answer_alert_rate is not None:
        new_th["view_answer_alert_rate"] = float(body.view_answer_alert_rate)
        thresholds_changed = True

    if thresholds_changed:
        save_analysis_thresholds(new_th)
        msgs.append("分析阈值已保存")

    # 2.5 沉淀参考开关（无需重建分析）
    if body.enable_qa_sediment_ref is not None:
        save_chat_flags({"enable_qa_sediment_ref": body.enable_qa_sediment_ref})
        msgs.append(
            "历史问答沉淀参考已"
            + ("开启" if body.enable_qa_sediment_ref else "关闭（AI 回答将不再参考历史问答，可节省 token）")
        )

    # 3. 阈值变了 → 热更新 analysis.config 缓存 + 重建学生聚合 + 重生成报告
    if thresholds_changed:
        try:
            from analysis.config import _cache as _analysis_cfg_cache
            _analysis_cfg_cache.clear()  # 清 analysis.config 模块级缓存（下次 load_config 重新读文件）
            # 再加一遍：直接调用 load_config 强制刷新
            from analysis.config import load_config as _analysis_load_config
            _analysis_load_config()
        except Exception as e:
            logger.warning("清 analysis.config 缓存异常: %s", e)

        try:
            from services.analysis_service import rebuild_agg_data
            rebuild_agg_data()
            msgs.append("学生聚合与画像已按新阈值重新计算（下次打开学生画像/报告即时生效）")
        except Exception as e:
            logger.warning("重建聚合分析失败: %s", e)
            msgs.append(f"⚠️ 分析数据重建失败：{str(e)[:60]}（不影响配置保存，重新上传数据后会自动生效）")

    return {
        "ok": True,
        "msg": "；".join(msgs) if msgs else "无更改",
        "analysis_rebuilt": thresholds_changed,
    }
