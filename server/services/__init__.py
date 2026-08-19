# _*_ coding : UTF-8 _*_
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .analysis_service import (
        list_data_files as _list_data_files,
        rebuild_agg_data as _rebuild_agg_data,
        restore_uploads_on_startup as _restore_uploads_on_startup,
        delete_data_file as _delete_data_file,
        get_file_for_download as _get_file_for_download,
    )
    from .upload_service import process_upload as _process_upload
    from .chat_service import (
        chat_stream as _chat_stream,
        chat_non_stream as _chat_non_stream,
        list_qa_logs as _list_qa_logs,
    )


_PKG = __name__  # "server.services"


def _lazy_import(name: str, module_name: str, obj_name: str) -> Any:
    import importlib
    import warnings
    try:
        if module_name.startswith("."):
            mod = importlib.import_module(module_name, package=_PKG)
        else:
            mod = importlib.import_module(module_name)
        return getattr(mod, obj_name)
    except Exception as e:  # pragma: no cover - import 失败只发生在可选依赖缺失时
        warnings.warn(f"[services] 延迟导入 {name} 失败: {type(e).__name__}: {e}")
        def _fail(*a, **kw):  # type: ignore[return-value]
            raise RuntimeError(
                f"{name} 未加载成功（缺失可选依赖），请安装完整 requirements.txt 依赖后重试。原始错误: {type(e).__name__}: {e}"
            )
        return _fail


list_data_files = _lazy_import("list_data_files", ".analysis_service", "list_data_files")
rebuild_agg_data = _lazy_import("rebuild_agg_data", ".analysis_service", "rebuild_agg_data")
restore_uploads_on_startup = _lazy_import("restore_uploads_on_startup", ".analysis_service", "restore_uploads_on_startup")
delete_data_file = _lazy_import("delete_data_file", ".analysis_service", "delete_data_file")
get_file_for_download = _lazy_import("get_file_for_download", ".analysis_service", "get_file_for_download")
process_upload = _lazy_import("process_upload", ".upload_service", "process_upload")
chat_stream = _lazy_import("chat_stream", ".chat_service", "chat_stream")
chat_non_stream = _lazy_import("chat_non_stream", ".chat_service", "chat_non_stream")
list_qa_logs = _lazy_import("list_qa_logs", ".chat_service", "list_qa_logs")

__all__ = [
    "list_data_files",
    "rebuild_agg_data",
    "restore_uploads_on_startup",
    "delete_data_file",
    "get_file_for_download",
    "process_upload",
    "chat_stream",
    "chat_non_stream",
    "list_qa_logs",
]
