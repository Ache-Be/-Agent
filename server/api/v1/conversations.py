# _*_ coding : UTF-8 _*_
"""
对话管理：CRUD 列表/详情/创建/更新/删除
"""
from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException, status

from core.utils import (
    list_conversations,
    get_conversation,
    save_conversation,
    delete_conversation,
    new_conv_id,
    auto_title,
)
from models.schemas import (
    Conversation,
    ConvCreateRequest,
    ConvUpdateRequest,
)
from datetime import datetime

router = APIRouter(prefix="/conversations", tags=["对话管理"])


@router.get("", response_model=List[Conversation])
async def api_list_conversations():
    return list_conversations()


@router.get("/{conv_id}", response_model=Conversation)
async def api_get_conversation(conv_id: str):
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "对话不存在")
    msg_count = len(conv.get("messages", []))
    return Conversation(
        id=conv["id"],
        title=conv.get("title", "新对话"),
        created_at=conv.get("created_at", ""),
        pinned=bool(conv.get("pinned", False)),
        msg_count=msg_count,
        messages=conv.get("messages", []),
    )


@router.post("", response_model=Conversation)
async def api_create_conversation(body: ConvCreateRequest | None = None):
    cid = new_conv_id()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = (body.title if body and body.title else "") or "新对话"
    conv = {"id": cid, "title": title, "created_at": now, "pinned": False, "messages": []}
    save_conversation(conv)
    return Conversation(
        id=cid, title=title, created_at=now, pinned=False, msg_count=0, messages=[],
    )


@router.patch("/{conv_id}")
async def api_update_conversation(conv_id: str, body: ConvUpdateRequest):
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "对话不存在")
    if body.title is not None:
        conv["title"] = body.title
    if body.pinned is not None:
        conv["pinned"] = bool(body.pinned)
    save_conversation(conv)
    return {"ok": True}


@router.delete("/{conv_id}")
async def api_delete_conversation(conv_id: str):
    delete_conversation(conv_id)
    return {"ok": True}
