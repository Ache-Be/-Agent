# _*_ coding : UTF-8 _*_
"""
AI 对话接口：流式 SSE + 非流式备用
"""
from typing import Any, Dict, Optional
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.chat_service import chat_stream, chat_non_stream, list_qa_logs

router = APIRouter(tags=["AI 教学助手"])


class ChatReq(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    stream: Optional[bool] = True


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


@router.get("/qa-sediment")
async def api_qa_sediment(limit: int = 100):
    """问答沉淀记录（教学经验库）"""
    return list_qa_logs(limit=limit)
