# _*_ coding : UTF-8 _*_
"""
AI 对话接口：流式 SSE + 非流式备用 + 报告 Word 下载
"""
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from core.config import config
from services.chat_service import (
    chat_stream,
    chat_non_stream,
    list_qa_logs,
    count_qa_sediment,
    clear_qa_sediment,
    list_qa_sediment,
    delete_qa_sediment,
)

router = APIRouter(tags=["AI 教学助手"])


class ChatReq(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    stream: Optional[bool] = True


class QaDeleteItem(BaseModel):
    source: str  # jsonl / pgvector
    id: str


class QaDeleteReq(BaseModel):
    items: List[QaDeleteItem]


@router.post("/chat")
async def api_chat(req: ChatReq, request: Request):
    """AI 对话：默认 SSE 流式返回。传 stream=false 返回普通 JSON。"""
    if req.stream is False:
        return chat_non_stream(req.message, req.conversation_id)

    def gen():
        yield from chat_stream(req.message, req.conversation_id)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/chat/report-download/{filename}")
async def api_report_download(filename: str):
    """下载 AI 生成的报告 Word 文档。
    安全：文件名取 Path.name 剥离路径分隔符，且只允许访问 ai_report_dir（与上传数据目录严格隔离），
    文件名必须 .docx 结尾，防路径穿越与数据文件暴露。"""
    fname = Path(filename).name
    if Path(fname).suffix.lower() != ".docx":
        raise HTTPException(status_code=404, detail="仅支持下载 AI 生成的 Word 报告")
    target = config.ai_report_dir / fname
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="报告文件不存在或已被清理")
    return FileResponse(
        str(target),
        filename=fname,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@router.get("/qa-sediment")
async def api_qa_sediment(limit: int = 100):
    """问答沉淀记录：合并 jsonl 旧库 + pgvector 新库，带 source 便于逐条管理"""
    return list_qa_sediment(limit=limit)


@router.get("/chat/qa-sediment-count")
async def api_qa_sediment_count():
    """问答沉淀各渠道条数（jsonl 旧库 + pgvector 新库）"""
    return count_qa_sediment()


@router.delete("/chat/qa-sediment")
async def api_delete_qa_sediment(body: Optional[QaDeleteReq] = None):
    """问答沉淀管理：
    - 传 body.items → 按 source+id 逐条删除
    - 不传 body → 清空全部（破坏性操作，前端需二次确认）"""
    if body is not None and body.items:
        return {"deleted": delete_qa_sediment(
            [{"source": i.source, "id": i.id} for i in body.items]
        )}
    return {"cleared": clear_qa_sediment()}
