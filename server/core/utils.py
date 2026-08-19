# _*_ coding : UTF-8 _*_
"""
通用工具函数：原 Flask app.py 里的零散函数
"""
import codecs
import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

from core.config import config
from core.logging_setup import logger
from core import state


def allowed_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in config.allow_ext


def file_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def looks_like_utf8(data: bytes, strict: bool = True) -> bool:
    """判断字节是否合法 UTF-8。strict=True 要求全量合法，strict=False 允许末尾截断字符"""
    try:
        data.decode("utf-8", errors="strict" if strict else "replace")
        return True
    except UnicodeDecodeError:
        return False


def detect_encoding(data: bytes) -> str:
    """
    检测 CSV/文本文件编码（简化版，覆盖教学场景 99% 情况）：
    优先 BOM：UTF-8-BOM → UTF-16 → UTF-32
    否则：UTF-8 合法 → UTF-8；其他一律按 GBK（Excel、WPS 导出 CSV 默认 GBK/GB18030）
    """
    if len(data) >= 3 and data[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    if len(data) >= 2 and data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    if len(data) >= 4 and data[:4] in (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"):
        return "utf-32"
    head = data[: 4 << 10]  # 拿前 4KB 猜就够
    try:
        head.decode("utf-8", errors="strict")
        return "utf-8"
    except UnicodeDecodeError:
        return "gbk"


def decode_text(data: bytes, force_encoding: str | None = None) -> tuple[str, str]:
    """
    解码 bytes → (text, actual_encoding)，尽量少抛异常。
    若检测编码失败，fallback: UTF-8→GBK→charmap，保证一定会拿到字符串。
    """
    enc = force_encoding or detect_encoding(data)
    candidates: list[str] = []
    for c in (enc, "utf-8", "utf-8-sig", "gbk", "gb18030", "latin-1"):
        if c not in candidates:
            candidates.append(c)
    last_err: Exception | None = None
    for try_enc in candidates:
        try:
            text = data.decode(try_enc, errors="replace")
            # 双校验：如果 try_enc 是 utf-8，但 decode(errors=replace) 后有大量 \ufffd 且头像是中文，基本说明不是 utf-8
            if try_enc in ("utf-8", "utf-8-sig"):
                head = text[:200]
                bad_count = head.count("\ufffd")
                if bad_count > 3 and ("\u4e00" <= head[-1] <= "\u9fff" if head else False):
                    continue
            return text, try_enc
        except Exception as e:
            last_err = e
            continue
    raise last_err or RuntimeError(f"无法解码，字节长度={len(data)}")


def to_utf8_bytes(data: bytes) -> tuple[bytes, str, str]:
    """
    把任意文本字节归一化成 UTF-8（无 BOM）bytes。
    返回: (utf8_bytes, original_encoding, note)
    """
    text, enc = decode_text(data)
    # 去 BOM（excel 导出的 utf-8-sig 会带 \ufeff）
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.encode("utf-8"), enc, (
        "原编码:UTF-8" if enc.startswith("utf-8") else f"原编码:{enc}→已自动转为UTF-8"
    )


def make_student_key(name: str = "", student_id: str = "") -> str:
    key = f"{name}_{student_id}" if student_id else name
    return "".join(c if c.isalnum() or c in " _-." else "_" for c in key)


def fmt_file_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


# ----------- API Key -----------
CONFIG_DIR = config.BASE_DIR / "config" if hasattr(config, "BASE_DIR") else config.log_dir.parent / "config"

# 实际使用 server/config/ 存放
_CONFIG_DIR = config.log_dir.parent / "config"
_CONFIG_FILE = _CONFIG_DIR / "settings.json"
_CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_api_key() -> str:
    if _CONFIG_FILE.exists():
        try:
            data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
            return data.get("deepseek_api_key", "")
        except Exception:
            return ""
    return ""


def save_api_key(key: str):
    data = {}
    if _CONFIG_FILE.exists():
        try:
            data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    data["deepseek_api_key"] = key
    _CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ----------- 分析阈值（analysis.config）读写 -----------
_DEFAULT_THRESHOLDS = {
    "weak_threshold": 0.7,
    "low_score_line": 60.0,
    "view_answer_alert_rate": 0.3,
    "exclude_names": ["卢冶"],
}


def _analysis_config_candidates() -> List[Path]:
    """阈值配置文件候选路径：server/config/settings.json 优先，旧 web/config 做兼容 fallback。"""
    from pathlib import Path as _P
    candidates: List[Path] = [_CONFIG_FILE]
    old_path = config.PROJECT_ROOT / "web" / "config" / "settings.json"
    if old_path.exists():
        candidates.append(old_path)
    return candidates


def load_analysis_thresholds() -> Dict[str, float]:
    """加载分析阈值（优先 server/config，其次旧 web/config，最后默认值）。"""
    cfg: Dict[str, float] = dict(_DEFAULT_THRESHOLDS)
    for fp in _analysis_config_candidates():
        if fp.exists():
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                continue
            for k in ("weak_threshold", "low_score_line", "view_answer_alert_rate"):
                v = data.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    cfg[k] = float(v)
            if isinstance(data.get("exclude_names"), list):
                cfg["exclude_names"] = [str(v).strip() for v in data["exclude_names"] if str(v).strip()]
            break
    return cfg


def save_analysis_thresholds(new_values: Dict[str, Any]):
    """把阈值写入 server/config/settings.json（与 API Key 同文件，统一管理）。"""
    data: Dict[str, Any] = {}
    if _CONFIG_FILE.exists():
        try:
            data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    for k in ("weak_threshold", "low_score_line", "view_answer_alert_rate"):
        v = new_values.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            data[k] = float(v)
    if isinstance(new_values.get("exclude_names"), list):
        data["exclude_names"] = [str(v).strip() for v in new_values["exclude_names"] if str(v).strip()]
    _CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ----------- AI 对话功能开关 -----------
_DEFAULT_CHAT_FLAGS = {"enable_qa_sediment_ref": True}


def load_chat_flags() -> Dict[str, Any]:
    """加载 AI 对话功能开关（当前仅 enable_qa_sediment_ref：回答时是否检索/注入历史问答沉淀）。"""
    cfg: Dict[str, Any] = dict(_DEFAULT_CHAT_FLAGS)
    if _CONFIG_FILE.exists():
        try:
            data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return cfg
        if isinstance(data.get("enable_qa_sediment_ref"), bool):
            cfg["enable_qa_sediment_ref"] = data["enable_qa_sediment_ref"]
    return cfg


def save_chat_flags(new_values: Dict[str, Any]):
    """保存 AI 对话功能开关到 server/config/settings.json（与 API Key 同文件，统一管理）。"""
    data: Dict[str, Any] = {}
    if _CONFIG_FILE.exists():
        try:
            data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    if isinstance(new_values.get("enable_qa_sediment_ref"), bool):
        data["enable_qa_sediment_ref"] = bool(new_values["enable_qa_sediment_ref"])
    _CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ----------- 对话管理 -----------
CONV_DIR = _CONFIG_DIR / "conversations"
CONV_DIR.mkdir(parents=True, exist_ok=True)

MAX_CONVERSATIONS = int(config.get("conv.max_conversations", 50))
HISTORY_KEEP_LAST = int(config.get("conv.history_keep_last", 20))


def _conv_path(cid: str) -> Path:
    return CONV_DIR / f"conv_{cid}.json"


def list_conversations() -> List[Dict[str, Any]]:
    convs: List[Dict[str, Any]] = []
    if CONV_DIR.exists():
        for f in sorted(CONV_DIR.glob("conv_*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                convs.append({
                    "id": data["id"],
                    "title": data.get("title", "新对话"),
                    "created_at": data.get("created_at", ""),
                    "pinned": bool(data.get("pinned", False)),
                    "msg_count": len(data.get("messages", [])),
                })
            except Exception:
                pass
    convs.sort(key=lambda c: c["created_at"], reverse=True)
    convs.sort(key=lambda c: c["pinned"], reverse=True)
    return convs


def get_conversation(cid: str) -> Optional[Dict[str, Any]]:
    fp = _conv_path(cid)
    if fp.exists():
        try:
            return json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def save_conversation(data: Dict[str, Any]):
    _conv_path(data["id"]).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    _trim_conversations()


def delete_conversation(cid: str):
    fp = _conv_path(cid)
    if fp.exists():
        fp.unlink()


def _trim_conversations():
    try:
        convs = list_conversations()
        pinned = [c for c in convs if c.get("pinned")]
        normal = [c for c in convs if not c.get("pinned")]
        overflow = len(normal) - (MAX_CONVERSATIONS - len(pinned))
        if overflow <= 0:
            return
        for c in normal[-overflow:]:
            delete_conversation(c["id"])
        logger.info("会话历史治理：删除 %d 个最旧会话", overflow)
    except Exception as e:
        logger.warning("会话历史治理失败：%s", e)


def new_conv_id() -> str:
    return uuid.uuid4().hex[:12]


def auto_title(messages: List[Dict[str, str]]) -> str:
    for m in messages:
        if m.get("role") == "user":
            t = m["content"].strip()[:30]
            return t + ("…" if len(m["content"]) > 30 else "")
    return "新对话"


# --------- 知识库备份 ---------
def backup_knowledge_base():
    src = config.knowledge_csv
    if not src.exists():
        return
    try:
        dst_dir = config.knowledge_backup_dir
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = dst_dir / f"knowledge_base_{stamp}.csv"
        shutil.copy2(str(src), str(dst))
        backups = sorted(dst_dir.glob("knowledge_base_*.csv"),
                         key=lambda p: p.stat().st_mtime)
        max_bak = int(config.get("knowledge.backup_max", 20))
        for old in backups[:-max_bak]:
            old.unlink()
        logger.info("知识库自动备份完成：%s（共 %d 份）", dst.name, len(backups))
    except Exception as e:
        logger.warning("知识库自动备份失败：%s", e)


# --------- 报告归档治理 ---------
def trim_report_archives():
    max_txt = int(config.get("report.max_txt_archive", 3000))
    try:
        txts = sorted(
            (p for p in config.report_dir.glob("*.txt") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
    except OSError:
        return
    if len(txts) <= max_txt:
        return
    excess = len(txts) - max_txt
    removed = 0
    for p in txts[:excess]:
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    logger.info("报告归档治理：清理 %d 个最旧 txt 报告", removed)


def safe_upload_path(filename: str) -> Optional[Path]:
    """返回 UPLOAD_DIR / REPORT_DIR 内真实存在的安全路径，越界或不存在返回 None"""
    for base in (config.upload_dir, config.report_dir):
        try:
            candidate = (base / filename).resolve()
        except (OSError, ValueError):
            continue
        try:
            is_inside = candidate.is_relative_to(base.resolve())
        except AttributeError:
            is_inside = str(candidate).startswith(str(base.resolve()))
        if is_inside and candidate.exists():
            return candidate
    return None
