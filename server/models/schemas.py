# _*_ coding : UTF-8 _*_
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ========= 通用响应 =========
class BaseResponse(BaseModel):
    code: int = 0
    msg: str = "ok"
    data: Optional[Any] = None


# ========= 健康检查 =========
class HealthzResponse(BaseModel):
    status: str
    time: str
    files: int
    has_analysis: bool
    conversations: int


# ========= 对话 =========
class Conversation(BaseModel):
    id: str
    title: str = "新对话"
    created_at: str = ""
    pinned: bool = False
    msg_count: int = 0
    messages: Optional[List[Dict[str, Any]]] = None


class ConvCreateRequest(BaseModel):
    title: Optional[str] = None


class ConvUpdateRequest(BaseModel):
    title: Optional[str] = None
    pinned: Optional[bool] = None


# ========= AI 对话 =========
class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None


# ========= 文件管理 =========
class DataFileItem(BaseModel):
    name: str
    size: int
    size_str: str
    mtime: str


class DataFileListResponse(BaseModel):
    files: List[DataFileItem]


class DataFileDeleteRequest(BaseModel):
    name: str


class DataFileBatchDeleteRequest(BaseModel):
    names: List[str]


# ========= 配置 =========
class ConfigRequest(BaseModel):
    api_key: Optional[str] = None


class ConfigResponse(BaseModel):
    configured: bool


# ========= 分析上下文 =========
class ContextResponse(BaseModel):
    has_data: bool
    total_students: int = 0
    weak_count: int = 0
    experiment_count: int = 0
    quiz_count: int = 0
    unit_count: int = 0
    attendance_count: int = 0
    file_count: int = 0


# ========= 问答沉淀 =========
class QaSedimentResponse(BaseModel):
    total: int
    logs: List[Any] = []


# ========= 上传 =========
class UploadResponse(BaseModel):
    ok: bool = True
    message: str = ""
    results: List[Any] = []
    rejected: List[str] = []
    skipped: List[str] = []
