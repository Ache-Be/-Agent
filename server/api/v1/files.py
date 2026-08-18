# _*_ coding : UTF-8 _*_
"""
数据文件管理
"""
from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File, Form, Body
from fastapi.responses import FileResponse
from starlette.background import BackgroundTasks
from typing import List, Optional

from services.analysis_service import (
    list_data_files,
    delete_data_file,
    delete_batch_data_files,
    delete_all_data_files,
    get_file_for_download,
)
from services.upload_service import process_upload
from models.schemas import (
    DataFileListResponse,
    DataFileDeleteRequest,
    DataFileBatchDeleteRequest,
    UploadResponse,
)

router = APIRouter(tags=["文件与上传"])


@router.get("/data_files", response_model=DataFileListResponse)
async def api_list_data_files():
    files = list_data_files()
    return DataFileListResponse(files=files)


@router.delete("/data_files")
async def api_delete_data_file(body: DataFileDeleteRequest):
    ok = delete_data_file(body.name)
    if not ok:
        raise HTTPException(400, "文件不存在或删除失败")
    return {"ok": True}


@router.post("/data_files/batch-delete")
async def api_batch_delete_data_files(body: DataFileBatchDeleteRequest):
    ok = delete_batch_data_files(body.names)
    if not ok:
        raise HTTPException(400, "批量删除失败")
    return {"ok": True}


@router.delete("/data_files/all")
async def api_delete_all_data_files():
    ok = delete_all_data_files()
    if not ok:
        raise HTTPException(400, "清空失败")
    return {"ok": True}


@router.post("/upload", response_model=UploadResponse)
async def api_upload(
    files: Optional[List[UploadFile]] = File(default=None),
    file: Optional[UploadFile] = File(default=None),
    relative_paths: Optional[List[str]] = Form(default=None),
    mode: str = Form("merge"),
):
    """
    多文件 / 单文件 都兼容的上传接口。
    - 批量上传（推荐）：Form 字段名 files[] 或 files 传多个文件。
    - 兼容旧 el-upload 单文件回调：Form 字段名 file。
    - relative_paths：和 files 长度对齐的相对路径数组（前端拖拽文件夹时
      每个 File 上的 webkitRelativePath，用来区分"校区软件1-3班/项目0.xlsx"
      和"校区软件4-5班/项目0.xlsx"这种同名不同文件夹的文件，避免覆盖存盘
      和 merge 判重丢失）。为空就退化成文件名。
    mode: 'merge'（默认）追加 / 'overwrite' 清空历史后重跑。
    """
    mode = mode if mode in ("merge", "overwrite") else "merge"
    all_uploads: List[UploadFile] = []
    if files:
        all_uploads.extend(files)
    if file:
        all_uploads.append(file)
    if not all_uploads:
        return UploadResponse(
            ok=False,
            message="未接收到任何上传文件，请重新选择",
            results=[],
            rejected=["未接收到文件（空请求或字段名不匹配）"],
            skipped=[],
        )

    # relative_paths 对齐：如果传了且长度等于 all_uploads，用它；否则用文件名补齐
    if not relative_paths:
        relative_paths_list: List[str] = [u.filename or "" for u in all_uploads]
    else:
        relative_paths_list = list(relative_paths)
        if len(relative_paths_list) < len(all_uploads):
            # 长度不够，后面用文件名补齐
            tail = [u.filename or "" for u in all_uploads[len(relative_paths_list):]]
            relative_paths_list.extend(tail)
        elif len(relative_paths_list) > len(all_uploads):
            relative_paths_list = relative_paths_list[: len(all_uploads)]

    data: List[tuple[str, bytes, str]] = []
    for i, f in enumerate(all_uploads):
        try:
            raw = await f.read()
            rel_path = relative_paths_list[i] or f.filename or "unknown"
            data.append((f.filename or "unknown", raw, rel_path))
        finally:
            await f.close()
    result = process_upload(data, mode=mode)
    return UploadResponse(
        ok=result.get("ok", False),
        message=result.get("message", ""),
        results=result.get("results", []),
        rejected=result.get("rejected", []),
        skipped=result.get("skipped", []),
    )


@router.get("/download/{filename:path}")
async def api_download(filename: str):
    fp = get_file_for_download(filename)
    if not fp:
        raise HTTPException(404, "文件不存在")
    media = "text/plain; charset=utf-8" if fp.suffix.lower() == ".txt" else "application/octet-stream"
    return FileResponse(str(fp), filename=fp.name, media_type=media)
