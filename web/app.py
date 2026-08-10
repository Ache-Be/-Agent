"""
教学预警系统 — Web 界面 v0.3

启动方式：
  python web/app.py

然后在浏览器打开 http://localhost:5000
"""

import sys
import os
import json
import time
import shutil
import hashlib
import logging
import logging.handlers
import threading
import codecs
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("teaching-warning")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# 日志轮转：10MB × 3 份，避免日志文件无限增长
LOG_DIR = ROOT / "web" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
_file_handler = logging.handlers.RotatingFileHandler(
    str(LOG_DIR / "teaching-warning.log"),
    maxBytes=10 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
logger.addHandler(_file_handler)

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, send_file, session, jsonify,
    Response, stream_with_context,
)
from werkzeug.utils import secure_filename

from analysis.knowledge_builder import load_knowledge_base
from analysis.knowledge_importer import import_knowledge_file
from analysis.analyzer import (
    analyze_touge_file,
    analyze_mooc_file,
    detect_data_source,
)
from analysis.reporter import generate_touge_report, generate_mooc_report
from analysis.question_parser import (
    parse_word_questions,
    match_questions_to_knowledge,
    generate_question_report,
)
from analysis.student_analyzer import (
    analyze_all_students,
    generate_student_report,
    generate_class_summary,
)
from analysis.cross_analyzer import (
    aggregate_students,
    generate_cross_student_report,
    generate_cross_summary,
)
from analysis.quiz_parser import analyze_quiz_file
from analysis.extra_parser import analyze_unit_file, analyze_attendance_file, analyze_knowledge_file
from web.ai_assistant import (
    build_analysis_context,
    chat_with_deepseek_stream,
    retrieve_knowledge,
    format_knowledge_ref,
)
from analysis.qa_sediment import (
    save_qa,
    retrieve_qa,
    format_qa_ref,
    load_qa_logs,
    count_qa,
)
from analysis.predictor import build_prediction_text

app = Flask(__name__)
app.secret_key = "teaching-warning-system-secret"
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

UPLOAD_DIR = ROOT / "web" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 报告 txt 归档子目录：主目录只保留原始数据文件（csv/xlsx），
# 汇总_/个人_/报告_/学生_ 等 txt 与 CSV 副本统一归档到这里，
# 启动恢复扫描主目录时自动跳过，避免文件越积越多、重复解析。
REPORT_DIR = UPLOAD_DIR / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# 报告归档治理：txt 报告超过上限时按修改时间删除最旧的（CSV 副本保留，可随时重新分析）
MAX_REPORT_TXT = 3000


def _trim_report_archives():
    """报告 txt 归档治理：超过 MAX_REPORT_TXT 时清理最旧的 txt（保留 CSV 副本）"""
    try:
        txts = sorted(
            (p for p in REPORT_DIR.glob("*.txt") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
    except OSError:
        return
    if len(txts) <= MAX_REPORT_TXT:
        return
    excess = len(txts) - MAX_REPORT_TXT
    removed = 0
    for p in txts[:excess]:
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    logger.info(
        "报告归档治理：清理 %d 个最旧 txt 报告（CSV 副本保留，可重新分析），当前剩余 %d",
        removed, len(txts) - removed,
    )

# 已上传文件的 MD5 内容哈希索引 {md5: original_name}，用于跨请求/跨启动去重
_file_hashes: dict = {}


def _file_md5(path) -> str:
    """计算文件内容 MD5（分块读取，避免大文件占内存）"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_upload_path(filename: str):
    """将 URL 中的文件名解析为 UPLOAD_DIR / REPORT_DIR 内【实际存在】的安全路径；越界或不存在返回 None"""
    for base in (UPLOAD_DIR, REPORT_DIR):
        try:
            candidate = (base / filename).resolve()
        except (OSError, ValueError):
            continue
        # 必须落在 base 内且文件真实存在（报告归档后位于 reports/ 子目录）
        if candidate.is_relative_to(base.resolve()) and candidate.exists():
            return candidate
    return None

TEMP_DIR = UPLOAD_DIR / "_temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_DIR = ROOT / "web" / "config"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = CONFIG_DIR / "settings.json"

# 加载知识点库
KNOWLEDGE_CSV = ROOT / "data" / "knowledge" / "knowledge_base.csv"
knowledge_base = None
if KNOWLEDGE_CSV.exists():
    knowledge_base = load_knowledge_base(str(KNOWLEDGE_CSV))

# 知识库自动备份：启动时保存带时间戳副本，保留最近 20 份，防人工误操作覆盖
KB_BACKUP_DIR = ROOT / "data" / "knowledge" / "backups"
KB_BACKUP_MAX = 20


def _backup_knowledge_base():
    """启动时备份 knowledge_base.csv，仅保留最近 KB_BACKUP_MAX 份"""
    if not KNOWLEDGE_CSV.exists():
        return
    try:
        KB_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = KB_BACKUP_DIR / f"knowledge_base_{stamp}.csv"
        shutil.copy2(str(KNOWLEDGE_CSV), str(dst))
        backups = sorted(KB_BACKUP_DIR.glob("knowledge_base_*.csv"),
                         key=lambda p: p.stat().st_mtime)
        for old in backups[:-KB_BACKUP_MAX]:
            old.unlink()
        logger.info("知识库自动备份完成：%s（共 %d 份）", dst.name, len(backups))
    except OSError as e:
        logger.warning("知识库自动备份失败: %s", e)


ALLOWED_EXTENSIONS = {".csv", ".docx", ".xlsx"}

# 全局：存储最近一次分析的上传文件列表和分析上下文（供 AI 对话使用）
_latest_results = []           # 最近一次上传的文件列表
_latest_agg_data = None        # 最近一次聚合分析结果
_latest_quiz_results = []      # 最近一次上传的随堂测验结果
_latest_experiment_results = []  # 累积的头歌实验结果（合并模式会追加）
_latest_unit_results = []        # 累积的单元练习结果（合并模式会追加）
_latest_attendance_results = []  # 累积的课堂活动分数明细结果（合并模式会追加）
_latest_knowledge_results = []   # 累积的知识点掌握度结果（合并模式会追加）
_chat_history = []             # 对话历史

# 并发安全：waitress 多线程下，所有修改全局分析状态的写操作串行化
_ANALYSIS_LOCK = threading.RLock()


def _analysis_locked(func):
    """装饰器：串行化上传/删除等写端点，防止多线程并发修改全局状态"""
    def wrapper(*args, **kwargs):
        with _ANALYSIS_LOCK:
            return func(*args, **kwargs)
    wrapper.__name__ = getattr(func, "__name__", "wrapper")
    return wrapper


def allowed_file(filename):
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def _looks_like_utf8(data: bytes) -> bool:
    """增量解码器检测是否为合法 UTF-8（容忍采样截断产生的半截多字节字符）"""
    try:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        decoder.decode(data, final=False)
        return True
    except UnicodeDecodeError:
        return False


def make_student_key(name: str = "", student_id: str = "") -> str:
    """生成唯一的学生标识，用于文件名和 URL（避免学号重复导致覆盖）"""
    key = f"{name}_{student_id}" if student_id else name
    return "".join(c if c.isalnum() or c in " _-." else "_" for c in key)


def load_api_key() -> str:
    """从配置文件加载 API Key"""
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return data.get("deepseek_api_key", "")
        except Exception:
            return ""
    return ""


def save_api_key(key: str):
    """保存 API Key 到配置文件"""
    data = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    data["deepseek_api_key"] = key
    CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ============================================================
#  主页面 — 新仪表盘（上传 + AI 对话）
# ============================================================
@app.route("/", methods=["GET"])
def index():
    # 只向前端透传"是否已配置"，绝不回显真实 Key，防止泄露
    api_configured = bool(load_api_key())
    return render_template("dashboard.html",
        files=_latest_results,
        has_analysis=bool(_latest_agg_data),
        api_configured=api_configured,
        initial_conv_id=request.args.get("conv", ""),
        # 区分"本次刚上传过数据"（上传成功后 redirect 带 just_uploaded=1）
        # 与"启动时自动恢复的历史数据"，避免误提示"已分析你上传的数据"
        user_uploaded=request.args.get("just_uploaded") == "1",
    )


# ============================================================
#  聚合分析 + 学生成绩预测（上传后/启动恢复时共用）
# ============================================================
SNAPSHOT_FILE = CONFIG_DIR / "analysis_snapshot.json"


def _current_upload_fingerprint() -> dict:
    """当前 UPLOAD_DIR 主目录原始数据文件的指纹 {文件名: md5}（用于校验快照是否过期）"""
    fp = {}
    if UPLOAD_DIR.exists():
        for f in UPLOAD_DIR.iterdir():
            if f.is_file() and f.suffix.lower() in (".csv", ".xlsx"):
                try:
                    fp[f.name] = _file_md5(f)
                except Exception:
                    fp[f.name] = "?"
    return fp


def _save_analysis_snapshot():
    """将当前分析结果与文件指纹序列化保存，供下次启动毫秒级恢复（免去重新解析全部原始文件）"""
    try:
        snapshot = {
            "fingerprint": _current_upload_fingerprint(),
            "latest_results": _latest_results,
            "agg_data": _latest_agg_data,
            "experiment_results": _latest_experiment_results,
            "quiz_results": _latest_quiz_results,
            "unit_results": _latest_unit_results,
            "attendance_results": _latest_attendance_results,
            "knowledge_results": _latest_knowledge_results,
        }
        SNAPSHOT_FILE.write_text(
            json.dumps(snapshot, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception as e:
        logger.warning("保存分析快照失败: %s", e)


def _try_restore_snapshot() -> bool:
    """启动时尝试从快照恢复分析结果；文件指纹一致才恢复，否则返回 False 走全量解析"""
    global _latest_results, _latest_agg_data, _latest_experiment_results
    global _latest_quiz_results, _latest_unit_results, _latest_attendance_results
    global _latest_knowledge_results
    if not SNAPSHOT_FILE.exists():
        return False
    try:
        snap = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
        if snap.get("fingerprint") != _current_upload_fingerprint():
            logger.info("快照与当前上传文件不一致，跳过快照恢复")
            return False
        _latest_results = snap.get("latest_results", [])
        _latest_agg_data = snap.get("agg_data")
        _latest_experiment_results = snap.get("experiment_results", [])
        _latest_quiz_results = snap.get("quiz_results", [])
        _latest_unit_results = snap.get("unit_results", [])
        _latest_attendance_results = snap.get("attendance_results", [])
        _latest_knowledge_results = snap.get("knowledge_results", [])
        logger.info("启动恢复：使用分析快照（%d 个文件，免重新解析）",
                    len(snap.get("fingerprint", {})))
        return True
    except Exception as e:
        logger.warning("读取分析快照失败，走全量解析: %s", e)
        return False


def _rebuild_agg_data():
    """根据当前 _latest_* 结果重新生成聚合分析与学生成绩预测。

    生成：学生综合汇总报告、个人报告、_latest_agg_data（含 prediction_text）。
    无数据时返回 None。
    """
    global _latest_agg_data
    if not (_latest_experiment_results or _latest_quiz_results
            or _latest_unit_results or _latest_attendance_results):
        return None
    agg = aggregate_students(_latest_experiment_results, knowledge_base,
                             quiz_results=_latest_quiz_results)
    agg_report = generate_cross_summary(agg)
    agg_filename = "学生综合汇总报告.txt"
    (REPORT_DIR / agg_filename).write_text(agg_report, encoding="utf-8")

    for sid, si in agg["students"].items():
        srep = generate_cross_student_report(si)
        safe_key = make_student_key(si.get("name", ""), sid)
        (REPORT_DIR / f"学生_{safe_key}.txt").write_text(srep, encoding="utf-8")

    student_list = []
    for s in agg["student_list"]:
        s["_key"] = make_student_key(s["name"], s["student_id"])
        student_list.append(s)
    top_error = [{"name": n, "error_count": i["error_count"],
                  "error_rate": i["error_rate"], "unit": i.get("unit", "")}
                 for n, i in agg.get("top_error_knowledge", [])]

    weak_count = sum(1 for s in student_list if s.get("weak_count", 0) > 0)

    # 总学生数：聚合结果可能为 0（未上传实验/测验时），回退到单元练习/课堂活动覆盖人数
    eff_total = agg["total_students"] or max(
        sum(u.get("student_count", 0) for u in _latest_unit_results),
        sum(a.get("student_count", 0) for a in _latest_attendance_results),
        0)

    _latest_agg_data = {
        "student_list": student_list,
        "top_error": top_error,
        "total_students": eff_total,
        "weak_student_count": weak_count,
        "experiment_count": agg["experiment_count"],
        "quiz_count": agg.get("quiz_count", 0),
        "unit_count": len(_latest_unit_results),
        "attendance_count": len(_latest_attendance_results),
        "agg_filename": agg_filename,
        # 完整学生聚合（含实验/随堂序列），供成绩预测使用
        "students": agg.get("students", {}),
    }
    # 学生成绩预测：基于历史趋势的统计估计（算一次，对话时直接注入）
    try:
        _latest_agg_data["prediction_text"] = build_prediction_text(
            _latest_agg_data["students"],
            unit_results=_latest_unit_results,
            attendance_results=_latest_attendance_results,
        )
    except Exception as e:
        logger.warning("生成成绩预测失败: %s", e)
        _latest_agg_data["prediction_text"] = ""
    _save_analysis_snapshot()
    return agg


def _restore_uploads_on_startup():
    """服务启动时自动恢复 uploads 目录中已上传的分析文件。

    只解析原始数据文件（*.csv / *.xlsx，跳过报告 txt），解析逻辑与 /upload
    路由一致；按 experiment_name / uploaded_name 去重合并，并重建聚合与预测。
    """
    global _latest_results, _latest_agg_data, _latest_quiz_results, _latest_experiment_results
    global _latest_unit_results, _latest_attendance_results, _latest_knowledge_results

    # 重建主目录原始文件的内容哈希索引（跨重启上传去重）
    global _file_hashes
    _file_hashes = {}
    if UPLOAD_DIR.exists():
        for f in UPLOAD_DIR.iterdir():
            if f.is_file() and f.suffix.lower() in (".csv", ".xlsx"):
                try:
                    _file_hashes[_file_md5(f)] = f.name
                except Exception:
                    pass

    # 优先使用分析快照：文件指纹一致时毫秒级恢复，免去重新解析全部原始文件
    if _try_restore_snapshot():
        logger.info("启动恢复：命中分析快照，跳过全量解析")
        return

    if not UPLOAD_DIR.exists():
        return
    skip_prefixes = ("汇总_", "个人_", "学生_", "报告_")
    data_files = sorted(
        f for f in UPLOAD_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in (".csv", ".xlsx")
        and not f.name.startswith(skip_prefixes)
    )
    if not data_files:
        return
    logger.info("启动恢复：发现 %d 个数据文件", len(data_files))

    experiment_results, quiz_results, unit_results = [], [], []
    attendance_results, knowledge_results = [], []
    restored_entries = []

    for f in data_files:
        original_name = f.name
        temp_path = TEMP_DIR / original_name
        try:
            shutil.copy2(str(f), str(temp_path))
        except Exception:
            continue
        entry = {"original_name": original_name, "source_type": "", "experiment_name": original_name,
                 "report_filename": "", "student_count": 0, "weak_count": 0, "has_error": False}
        try:
            if f.suffix.lower() == ".xlsx":
                fn = original_name.lower()
                if "单元练习" in fn:
                    res = analyze_unit_file(str(temp_path))
                    res["uploaded_name"] = original_name
                    unit_results.append(res)
                    entry["source_type"] = "单元练习"
                    entry["experiment_name"] = res.get("class_name", original_name)
                    entry["student_count"] = res.get("student_count", 0)
                elif "课堂活动" in fn or "分数明细" in fn:
                    res = analyze_attendance_file(str(temp_path))
                    res["uploaded_name"] = original_name
                    attendance_results.append(res)
                    entry["source_type"] = "课堂活动"
                    entry["experiment_name"] = res.get("class_name", original_name)
                    entry["student_count"] = res.get("student_count", 0)
                else:
                    try:
                        quiz = analyze_quiz_file(str(temp_path))
                    except Exception:
                        quiz = None
                    if quiz:
                        quiz["uploaded_name"] = original_name
                        quiz_results.append(quiz)
                        entry["source_type"] = "随堂测验"
                        entry["experiment_name"] = f"{quiz.get('project_id', '')}-{quiz.get('chapter_name', original_name)}"
                        entry["student_count"] = quiz.get("student_count", 0)
                        entry["weak_count"] = quiz.get("weak_count", 0)
                    else:
                        try:
                            res = analyze_unit_file(str(temp_path))
                            res["uploaded_name"] = original_name
                            unit_results.append(res)
                            entry["source_type"] = "单元练习"
                            entry["experiment_name"] = res.get("class_name", original_name)
                            entry["student_count"] = res.get("student_count", 0)
                        except Exception:
                            try:
                                res = analyze_attendance_file(str(temp_path))
                                res["uploaded_name"] = original_name
                                attendance_results.append(res)
                                entry["source_type"] = "课堂活动"
                                entry["experiment_name"] = res.get("class_name", original_name)
                                entry["student_count"] = res.get("student_count", 0)
                            except Exception:
                                res = analyze_knowledge_file(str(temp_path))
                                res["uploaded_name"] = original_name
                                knowledge_results.append(res)
                                entry["source_type"] = "知识点掌握度"
                                entry["experiment_name"] = res.get("class_name", original_name)
                                entry["student_count"] = res.get("student_count", 0)
            else:
                source_type = detect_data_source(str(temp_path))
                entry["source_type"] = source_type
                if source_type in ("touge", "auto"):
                    try:
                        result = analyze_touge_file(str(temp_path), knowledge_base)
                        entry["experiment_name"] = result.get("experiment_name", original_name)
                        entry["student_count"] = result.get("student_count", 0)
                        experiment_results.append(result)
                    except Exception as e:
                        entry["has_error"] = True
                        entry["error_msg"] = str(e)
        except Exception as e:
            entry["has_error"] = True
            entry["error_msg"] = str(e)
            logger.warning("启动恢复解析失败 [%s]: %s", original_name, e)
        finally:
            temp_path.unlink(missing_ok=True)
        if not entry["has_error"] and entry["source_type"]:
            restored_entries.append(entry)

    # 合并去重（与 /upload 一致）
    for exp in experiment_results:
        if not any(e.get("experiment_name") == exp.get("experiment_name")
                   for e in _latest_experiment_results):
            _latest_experiment_results.append(exp)
    for qz in quiz_results:
        if not any(q.get("uploaded_name") == qz.get("uploaded_name")
                   for q in _latest_quiz_results):
            _latest_quiz_results.append(qz)
    for ur in unit_results:
        if not any(u.get("uploaded_name") == ur.get("uploaded_name")
                   for u in _latest_unit_results):
            _latest_unit_results.append(ur)
    for ar in attendance_results:
        if not any(a.get("uploaded_name") == ar.get("uploaded_name")
                   for a in _latest_attendance_results):
            _latest_attendance_results.append(ar)
    for kr in knowledge_results:
        if not any(k.get("uploaded_name") == kr.get("uploaded_name")
                   for k in _latest_knowledge_results):
            _latest_knowledge_results.append(kr)

    if restored_entries:
        _latest_results = restored_entries

    _rebuild_agg_data()
    logger.info("启动恢复完成：实验 %d、随堂 %d、单元 %d、课堂 %d、知识点 %d，学生 %d",
                len(_latest_experiment_results), len(_latest_quiz_results),
                len(_latest_unit_results), len(_latest_attendance_results),
                len(_latest_knowledge_results),
                (_latest_agg_data or {}).get("total_students", 0))


# ============================================================
#  文件上传
# ============================================================
@app.route("/upload", methods=["POST"])
@_analysis_locked
def upload_files():
    global _latest_results, _latest_agg_data, _latest_quiz_results, _latest_experiment_results
    global _latest_unit_results, _latest_attendance_results, _latest_knowledge_results, _chat_history

    files = request.files.getlist("file")
    if not files or files[0].filename == "":
        flash("请选择要上传的文件（CSV / XLSX / DOCX）", "error")
        return redirect(url_for("index", conv=request.form.get("conversation_id", "")))

    # 上传模式：merge=合并到已有分析（默认）；overwrite=覆盖
    mode = request.form.get("mode", "merge")

    # 上传前所在的对话 ID：上传后回到该对话，继续追加提问
    conv_id = request.form.get("conversation_id", "")

    results = []
    quiz_results = []
    unit_results = []
    attendance_results = []
    knowledge_results = []
    skipped_duplicates = []
    rejected = []
    for file in files:
        if file.filename == "" or not allowed_file(file.filename):
            continue

        original_name = file.filename
        safe_name = secure_filename(original_name)
        store_path = UPLOAD_DIR / safe_name

        # 内容级校验：空文件 / 损坏的 xlsx、docx（非 ZIP 头）/ 非 UTF-8 的 CSV 直接拒绝
        file.stream.seek(0)
        head = file.stream.read(1024)
        file.stream.seek(0)
        if not head.strip():
            rejected.append(f"{original_name}（空文件）")
            continue
        suffix = Path(original_name).suffix.lower()
        if suffix in (".xlsx", ".docx") and not head.startswith(b"PK"):
            rejected.append(f"{original_name}（文件损坏或不是有效的 {suffix[1:].upper()} 文件）")
            continue
        if suffix == ".csv":
            # 用增量解码器检测编码：容忍采样截断产生的半截多字节字符，
            # 只对真正的非法字节序列判为编码不支持（兼容 UTF-8 带/不带 BOM）
            if not _looks_like_utf8(head):
                rejected.append(f"{original_name}（编码不支持，请使用 UTF-8 编码的 CSV）")
                continue

        # 内容级去重：MD5 与已上传文件相同则跳过保存与解析，避免文件越积越多
        file.stream.seek(0)
        digest = hashlib.md5(file.stream.read()).hexdigest()
        file.stream.seek(0)
        if digest in _file_hashes and _file_hashes[digest] != safe_name:
            skipped_duplicates.append(original_name)
            continue

        file.save(str(store_path))
        _file_hashes[digest] = safe_name
        temp_path = TEMP_DIR / original_name
        shutil.copy2(str(store_path), str(temp_path))

        entry = {"original_name": original_name, "source_type": "", "experiment_name": original_name,
                 "report_filename": "", "student_count": 0, "weak_count": 0, "has_error": False}

        # ---- xlsx：随堂测验 / 单元练习 / 课堂活动分数明细 ----
        if Path(original_name).suffix.lower() == ".xlsx":
            fn = original_name.lower()
            try:
                if "单元练习" in fn:
                    res = analyze_unit_file(str(temp_path))
                    res["uploaded_name"] = original_name
                    unit_results.append(res)
                    entry["source_type"] = "单元练习"
                    entry["experiment_name"] = res.get("class_name", original_name)
                    entry["student_count"] = res.get("student_count", 0)
                elif "课堂活动" in fn or "分数明细" in fn:
                    res = analyze_attendance_file(str(temp_path))
                    res["uploaded_name"] = original_name
                    attendance_results.append(res)
                    entry["source_type"] = "课堂活动"
                    entry["experiment_name"] = res.get("class_name", original_name)
                    entry["student_count"] = res.get("student_count", 0)
                else:
                    # 优先按随堂测验解析；失败再依次尝试 单元练习 / 课堂活动 / 知识点掌握度（兼容不同命名）
                    try:
                        quiz = analyze_quiz_file(str(temp_path))
                    except Exception:
                        quiz = None
                    if quiz:
                        quiz["uploaded_name"] = original_name
                        quiz_results.append(quiz)
                        entry["source_type"] = "随堂测验"
                        entry["experiment_name"] = f"{quiz.get('project_id', '')}-{quiz.get('chapter_name', original_name)}"
                        entry["student_count"] = quiz.get("student_count", 0)
                        entry["weak_count"] = quiz.get("weak_count", 0)
                    else:
                        try:
                            res = analyze_unit_file(str(temp_path))
                            res["uploaded_name"] = original_name
                            unit_results.append(res)
                            entry["source_type"] = "单元练习"
                            entry["experiment_name"] = res.get("class_name", original_name)
                            entry["student_count"] = res.get("student_count", 0)
                        except Exception:
                            try:
                                res = analyze_attendance_file(str(temp_path))
                                res["uploaded_name"] = original_name
                                attendance_results.append(res)
                                entry["source_type"] = "课堂活动"
                                entry["experiment_name"] = res.get("class_name", original_name)
                                entry["student_count"] = res.get("student_count", 0)
                            except Exception:
                                res = analyze_knowledge_file(str(temp_path))
                                res["uploaded_name"] = original_name
                                knowledge_results.append(res)
                                entry["source_type"] = "知识点掌握度"
                                entry["experiment_name"] = res.get("class_name", original_name)
                                entry["student_count"] = res.get("student_count", 0)
            except Exception as e:
                entry["has_error"] = True
                entry["error_msg"] = str(e)
                logger.warning("解析 XLSX 失败 [%s]: %s", original_name, e)
            results.append(entry)
            temp_path.unlink(missing_ok=True)
            continue

        try:
            source_type = detect_data_source(str(temp_path))
            entry["source_type"] = source_type

            if source_type == "touge":
                result = analyze_touge_file(str(temp_path), knowledge_base)
                report = generate_touge_report(result)
                entry["experiment_name"] = result.get("experiment_name", original_name)

                # 学生级分析
                student_analysis = analyze_all_students(result, knowledge_base)
                class_summary = generate_class_summary(student_analysis)
                report_stem = "".join(c if c.isalnum() or c in " -_" else "_" for c in Path(original_name).stem)

                # 保存班级汇总（归档到 reports 子目录）
                summary_fn = f"汇总_{report_stem}.txt"
                (REPORT_DIR / summary_fn).write_text(class_summary, encoding="utf-8")

                # 保存个人报告（归档到 reports 子目录）
                for sr in student_analysis.get("student_results", []):
                    name = sr.get("name", "")
                    sid = sr.get("student_id", "")
                    safe_key = make_student_key(name, sid)
                    sreport = generate_student_report(sr, result.get("experiment_name", ""))
                    (REPORT_DIR / f"个人_{report_stem}_{safe_key}.txt").write_text(sreport, encoding="utf-8")

                entry["student_count"] = result.get("student_count", 0)
                entry["weak_count"] = student_analysis.get("weak_student_count", 0)
                entry["report_stem"] = report_stem

                # 保存 CSV 副本（report_stem 命名，归档到 reports 子目录，避免启动恢复时重复解析）
                try:
                    (REPORT_DIR / f"{report_stem}.csv").write_bytes(store_path.read_bytes())
                except Exception:
                    pass

            elif source_type == "mooc":
                result = analyze_mooc_file(str(temp_path), knowledge_base)
                report = generate_mooc_report(result)
                entry["experiment_name"] = result.get("classroom_name", original_name)

            else:
                # auto-detect
                try:
                    result = analyze_touge_file(str(temp_path), knowledge_base)
                    report = generate_touge_report(result)
                    entry["experiment_name"] = result.get("experiment_name", original_name)
                    entry["source_type"] = "touge"
                    entry["student_count"] = result.get("student_count", 0)
                except Exception:
                    result = analyze_mooc_file(str(temp_path), knowledge_base)
                    report = generate_mooc_report(result)
                    entry["experiment_name"] = result.get("classroom_name", original_name)
                    entry["source_type"] = "mooc"

            # 保存报告（归档到 reports 子目录）
            report_stem = "".join(c if c.isalnum() or c in " -_" else "_" for c in Path(original_name).stem)
            report_fn = f"报告_{report_stem}.txt"
            (REPORT_DIR / report_fn).write_text(report, encoding="utf-8")
            entry["report_filename"] = report_fn

        except Exception as e:
            entry["has_error"] = True
            entry["error_msg"] = str(e)
            logger.warning("解析文件失败 [%s]: %s", original_name, e, exc_info=True)
        finally:
            temp_path.unlink(missing_ok=True)

        results.append(entry)

    if not results:
        if rejected:
            flash("以下文件未通过校验，已拒绝：" + "；".join(rejected), "error")
        elif skipped_duplicates:
            flash(f"所选文件均已上传过（{len(skipped_duplicates)} 个重复文件已跳过），无需重复分析", "error")
        else:
            flash("没有可分析的文件", "error")
        return redirect(url_for("index", conv=conv_id))

    _latest_results = results

    # 解析本次上传的头歌实验 CSV（填充 experiment_results）
    touge_results = [r for r in results if r["source_type"] == "touge" and not r["has_error"]]
    experiment_results = []
    for r in touge_results:
        try:
            stem = ".".join(r["report_filename"].split(".")[:-1])
            filepath = REPORT_DIR / f"{stem}.csv"
            if not filepath.exists():
                filepath = UPLOAD_DIR / secure_filename(r["original_name"])
            if not filepath.exists() and r.get("report_stem"):
                filepath = REPORT_DIR / f"{r['report_stem']}.csv"
            if filepath.exists():
                exp = analyze_touge_file(str(filepath), knowledge_base)
                experiment_results.append(exp)
        except Exception:
            pass

    # 覆盖模式：清空历史分析；合并模式：追加（按文件名去重）
    if mode == "overwrite":
        _latest_experiment_results = experiment_results
        _latest_quiz_results = quiz_results
        _latest_unit_results = unit_results
        _latest_attendance_results = attendance_results
        _latest_knowledge_results = knowledge_results
    else:  # 默认合并
        for exp in experiment_results:
            if not any(e.get("experiment_name") == exp.get("experiment_name")
                       for e in _latest_experiment_results):
                _latest_experiment_results.append(exp)
        for qz in quiz_results:
            if not any(q.get("uploaded_name") == qz.get("uploaded_name")
                       for q in _latest_quiz_results):
                _latest_quiz_results.append(qz)
        for ur in unit_results:
            if not any(u.get("uploaded_name") == ur.get("uploaded_name")
                       for u in _latest_unit_results):
                _latest_unit_results.append(ur)
        for ar in attendance_results:
            if not any(a.get("uploaded_name") == ar.get("uploaded_name")
                       for a in _latest_attendance_results):
                _latest_attendance_results.append(ar)
        for kr in knowledge_results:
            if not any(k.get("uploaded_name") == kr.get("uploaded_name")
                       for k in _latest_knowledge_results):
                _latest_knowledge_results.append(kr)

    # 聚合分析（头歌实验 + 随堂测验 + 单元练习 + 课堂活动，支持增量合并）
    # 同时生成学生成绩预测（基于历史趋势的统计估计）
    agg = _rebuild_agg_data()

    # 上传结果提示（含数据范围说明）
    if agg:
        action = "合并" if mode != "overwrite" else "覆盖"
        detail_parts = []
        if agg["experiment_count"]:
            detail_parts.append(f"{agg['experiment_count']} 个实验")
        if agg.get("quiz_count", 0):
            detail_parts.append(f"{agg.get('quiz_count', 0)} 次随堂测验")
        if len(_latest_unit_results):
            detail_parts.append(f"{len(_latest_unit_results)} 份单元练习")
        if len(_latest_attendance_results):
            detail_parts.append(f"{len(_latest_attendance_results)} 份课堂活动分数明细")
        msg = f"已{action}分析" + "、".join(detail_parts)
        eff_total = (_latest_agg_data or {}).get("total_students", 0)
        if eff_total:
            msg += f"，共 {eff_total} 名学生"
        if agg["experiment_count"] == 0:
            msg += "；未包含头歌实验数据（实验级分析不可用）"
        if agg.get("quiz_count", 0) == 0:
            msg += "；未包含随堂测验数据（章节级分析不可用）"
        if skipped_duplicates:
            msg += f"；{len(skipped_duplicates)} 个文件已存在，未重复导入"
        if rejected:
            msg += "；已拒绝损坏文件：" + "；".join(rejected)
        flash(msg, "success")
    elif results and skipped_duplicates:
        # 部分重复且无聚合结果（如全部解析失败）时，也给出去重提示
        flash(f"已解析 {len(results)} 个文件；{len(skipped_duplicates)} 个文件已存在，未重复导入", "success")

    return redirect(url_for("index", conv=conv_id, just_uploaded=1))


# ============================================================
#  多对话管理
# ============================================================
import uuid as uuid_mod
from datetime import datetime

CONV_DIR = CONFIG_DIR / "conversations"
CONV_DIR.mkdir(parents=True, exist_ok=True)


def _conv_path(cid: str) -> Path:
    return CONV_DIR / f"conv_{cid}.json"


def list_conversations():
    """列出所有对话（不含完整消息）：置顶优先，其余按日期降序"""
    convs = []
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
    # 置顶对话整体排最前；普通对话按创建时间倒序（稳定排序）
    convs.sort(key=lambda c: c["created_at"], reverse=True)
    convs.sort(key=lambda c: c["pinned"], reverse=True)
    return convs


def get_conversation(cid: str):
    """获取单个对话（含完整消息）"""
    fp = _conv_path(cid)
    if fp.exists():
        return json.loads(fp.read_text(encoding="utf-8"))
    return None


def save_conversation(data: dict):
    fp = _conv_path(data["id"])
    fp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    _trim_conversations()


def delete_conversation(cid: str):
    fp = _conv_path(cid)
    if fp.exists():
        fp.unlink()


# 会话历史上限：超过 MAX_CONVERSATIONS 个对话时，自动删除最旧的未置顶会话
MAX_CONVERSATIONS = 50


def _trim_conversations():
    """会话历史治理：超限时删除最旧的未置顶会话，防止目录无限增长"""
    try:
        convs = list_conversations()
        pinned = [c for c in convs if c.get("pinned")]
        normal = [c for c in convs if not c.get("pinned")]
        overflow = len(normal) - (MAX_CONVERSATIONS - len(pinned))
        if overflow <= 0:
            return
        for c in normal[-overflow:]:
            delete_conversation(c["id"])
        logger.info("会话历史治理：删除 %d 个最旧会话（上限 %d，当前共 %d）",
                    overflow, MAX_CONVERSATIONS, len(convs))
    except Exception as e:
        logger.warning("会话历史治理失败: %s", e)


def _auto_title(messages) -> str:
    """从第一条用户消息生成对话标题"""
    for m in messages:
        if m.get("role") == "user":
            t = m["content"].strip()[:30]
            return t + ("…" if len(m["content"]) > 30 else "")
    return "新对话"


# ============================================================
#  API: 对话列表 & 管理
# ============================================================
@app.route("/api/conversations", methods=["GET"])
def api_conv_list():
    return jsonify({"conversations": list_conversations()})


@app.route("/api/conversations", methods=["POST"])
def api_conv_create():
    cid = uuid_mod.uuid4().hex[:12]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conv = {"id": cid, "title": "新对话", "created_at": now, "pinned": False, "messages": []}
    save_conversation(conv)
    return jsonify({"id": cid, "title": "新对话", "created_at": now, "pinned": False})


@app.route("/api/conversations/<cid>", methods=["GET"])
def api_conv_get(cid):
    conv = get_conversation(cid)
    if not conv:
        return jsonify({"error": "not found"}), 404
    return jsonify(conv)


@app.route("/api/conversations/<cid>", methods=["PATCH"])
def api_conv_update(cid):
    """更新对话：支持改标题（title）、置顶/取消置顶（pinned）"""
    conv = get_conversation(cid)
    if not conv:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    if "title" in data:
        t = str(data["title"]).strip()
        if t:
            conv["title"] = t
    if "pinned" in data:
        conv["pinned"] = bool(data["pinned"])
    save_conversation(conv)
    return jsonify({"ok": True, "title": conv["title"], "pinned": bool(conv.get("pinned", False))})


@app.route("/api/conversations/<cid>", methods=["DELETE"])
def api_conv_delete(cid):
    delete_conversation(cid)
    return jsonify({"ok": True})


# ============================================================
#  API: 数据文件管理（列表 & 删除）
# ============================================================
def _fmt_file_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def _list_data_files():
    """列出 uploads 主目录下的原始数据文件（csv/xlsx），不含归档与临时目录"""
    files = []
    if UPLOAD_DIR.exists():
        for f in sorted(UPLOAD_DIR.iterdir(), key=lambda x: x.name.lower()):
            if f.is_file() and f.suffix.lower() in (".csv", ".xlsx"):
                try:
                    st = f.stat()
                    files.append({
                        "name": f.name,
                        "size": st.st_size,
                        "size_str": _fmt_file_size(st.st_size),
                        "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    })
                except OSError:
                    continue
    return files


@app.route("/api/data_files", methods=["GET"])
def api_data_files_list():
    return jsonify({"files": _list_data_files()})


@app.route("/healthz")
def healthz():
    """健康检查端点：供部署探活 / 监控使用"""
    return jsonify({
        "status": "ok",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": len(_list_data_files()),
        "has_analysis": bool(_latest_agg_data),
        "conversations": len(list_conversations()),
    })


@app.route("/api/data_files", methods=["DELETE"])
@_analysis_locked
def api_data_files_delete():
    """删除已上传的数据文件：移除主文件、内存结果、归档报告，并重建聚合与快照"""
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"error": "缺少文件名"}), 400
    target = UPLOAD_DIR / os.path.basename(name)
    if target.parent != UPLOAD_DIR or not target.is_file():
        return jsonify({"error": "文件不存在"}), 404

    global _file_hashes
    global _latest_results, _latest_agg_data, _latest_experiment_results
    global _latest_quiz_results, _latest_unit_results, _latest_attendance_results
    global _latest_knowledge_results

    # 找出该文件关联的实验名（用于清理 touge 实验聚合结果）
    removed_experiments = set()
    for e in _latest_results:
        if e.get("original_name") == name and e.get("experiment_name"):
            removed_experiments.add(e["experiment_name"])

    try:
        target.unlink()
    except OSError as exc:
        return jsonify({"error": f"删除失败：{exc}"}), 500

    _file_hashes = {h: fn for h, fn in _file_hashes.items() if fn != name}
    _latest_results = [e for e in _latest_results if e.get("original_name") != name]
    if removed_experiments:
        _latest_experiment_results = [
            e for e in _latest_experiment_results if e.get("experiment_name") not in removed_experiments
        ]
    _latest_quiz_results = [e for e in _latest_quiz_results if e.get("uploaded_name") != name]
    _latest_unit_results = [e for e in _latest_unit_results if e.get("uploaded_name") != name]
    _latest_attendance_results = [e for e in _latest_attendance_results if e.get("uploaded_name") != name]
    _latest_knowledge_results = [e for e in _latest_knowledge_results if e.get("uploaded_name") != name]

    # 清理归档报告（uploads/reports/ 下）
    stem = "".join(c if c.isalnum() or c in " -_" else "_" for c in Path(name).stem)
    for pat in (f"报告_{stem}.txt", f"汇总_{stem}.txt", f"{stem}.csv"):
        fp = REPORT_DIR / pat
        try:
            if fp.exists():
                fp.unlink()
        except OSError:
            pass
    for fp in REPORT_DIR.glob(f"个人_{stem}_*.txt"):
        try:
            fp.unlink()
        except OSError:
            pass

    # 重建聚合分析并刷新快照；无剩余数据时清空聚合
    if not (_latest_experiment_results or _latest_quiz_results
            or _latest_unit_results or _latest_attendance_results):
        _latest_agg_data = None
        _save_analysis_snapshot()
    else:
        _rebuild_agg_data()
    return jsonify({"ok": True, "files": _list_data_files()})


# ============================================================
#  API: AI 对话（绑定 conversation_id）
# ============================================================
def _recent_upload_context() -> str:
    """生成最近一次上传数据的分析摘要，供 AI 优先参考（历史上传与知识库仅作补充）"""
    lines = []
    for e in _latest_results:
        name = e.get("original_name", "未知文件")
        if e.get("has_error"):
            lines.append(f"- {name}：解析失败（{e.get('error_msg', '')}）")
            continue
        stype = e.get("source_type", "") or "未知类型"
        parts = [f"- {name}（{stype}"]
        if e.get("experiment_name") and e["experiment_name"] != name:
            parts.append(f"实验/班级：{e['experiment_name']}")
        if e.get("student_count"):
            parts.append(f"学生数：{e['student_count']}")
        if e.get("weak_count"):
            parts.append(f"薄弱学生数：{e['weak_count']}")
        if stype == "随堂测验":
            for q in _latest_quiz_results:
                if q.get("uploaded_name") == name:
                    if q.get("avg_accuracy") is not None:
                        parts.append(f"平均正确率：{q['avg_accuracy']}%")
                    if q.get("weak_rate") is not None:
                        parts.append(f"薄弱率：{q['weak_rate']}%")
                    break
        lines.append("，".join(parts) + "）")
    if not lines:
        return ""
    return (
        "【最近上传数据分析（回答时请优先参考）】\n"
        "以下为最近一次上传的数据文件及分析摘要，请优先以这部分数据回答用户当前问题，"
        "历史上传数据与知识库内容仅作为补充参考。\n"
        + "\n".join(lines)
    )


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True)
    if not data or not data.get("message"):
        return jsonify({"reply": "消息不能为空"}), 400

    user_msg = data["message"].strip()
    conv_id = data.get("conversation_id", "")
    api_key = load_api_key()

    # 获取或创建对话
    conv = get_conversation(conv_id) if conv_id else None
    if not conv:
        cid = uuid_mod.uuid4().hex[:12]
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        conv = {"id": cid, "title": "新对话", "created_at": now, "messages": []}

    # 构建系统提示（含分析上下文）
    system_content = (
        "你是一位教学预警系统的 AI 教学助手，职责是帮助教师分析学生实验数据。回答简洁、以数据为依据。"
        "如果当前没有可用的分析数据，或用户询问的数据不在你掌握的结果范围内，请如实告知"
        "（说明缺少哪部分数据），并提示用户先上传相应的 CSV/XLSX 数据文件，严禁编造学生成绩。"
        "学生姓名与学号属于教学分析数据：当用户询问学生名单、点名或具体学生时，"
        "可直接依据上下文中的『全部学生名单』如实列出，无需以隐私为由拒绝。"
        "当用户要求生成报告（如班级整体学习情况、学生个人报告、薄弱项分析等）时，"
        "请以清晰的 Markdown 结构输出：使用标题层级、表格、要点列表组织内容，"
        "便于系统将其导出为 Word 文档；报告末尾可提醒用户点击消息下方的『导出 Word』按钮保存为文档。"
    )
    if _latest_agg_data:
        ad = _latest_agg_data
        context = build_analysis_context(
            student_list=ad.get("student_list", []),
            top_error=ad.get("top_error", []),
            total_students=ad.get("total_students", 0),
            weak_count=ad.get("weak_student_count", 0),
            experiment_count=ad.get("experiment_count", 0),
            quiz_count=ad.get("quiz_count", 0),
            unit_results=_latest_unit_results,
            attendance_results=_latest_attendance_results,
            prediction_text=ad.get("prediction_text", ""),
        )
        system_content = f"{system_content}\n\n{context}"

    # 最近一次上传的数据：用户上传后立即提问的场景，优先参考本次上传
    recent_ctx = _recent_upload_context()
    if recent_ctx:
        system_content = f"{system_content}\n\n{recent_ctx}"

    # 轻量 RAG：按当前问题从知识点库检索相关条目，注入作为参考资料
    hit_records = retrieve_knowledge(user_msg, knowledge_base)
    kb_ref = format_knowledge_ref(hit_records)
    if kb_ref:
        system_content = f"{system_content}\n\n{kb_ref}"

    # 问答沉淀：检索历史相似问答，参考之前的回答经验（越问越准）
    qa_ref = format_qa_ref(retrieve_qa(user_msg, top_k=2))
    if qa_ref:
        system_content = f"{system_content}\n\n{qa_ref}"

    # 构建 messages
    messages = [{"role": "system", "content": system_content}]
    for h in conv["messages"][-20:]:
        messages.append(h)
    messages.append({"role": "user", "content": user_msg})

    def generate():
        """SSE 流式生成：先发用户消息落库事件，再逐段输出 AI 回复"""
        # 先写入用户消息（前端已展示，这里保证落盘一致）
        conv["messages"].append({"role": "user", "content": user_msg})

        # 首次响应：告知前端会话已建立（含 conversation_id）
        yield f"data: {json.dumps({'event': 'init', 'conversation_id': conv['id']}, ensure_ascii=False)}\n\n"

        # 流式调用 DeepSeek
        reply_parts = []
        try:
            for chunk in chat_with_deepseek_stream(api_key, messages):
                reply_parts.append(chunk)
                yield f"data: {json.dumps({'event': 'delta', 'delta': chunk}, ensure_ascii=False)}\n\n"
        except Exception as e:
            err = f"请求出错：{str(e)}"
            reply_parts.append(err)
            yield f"data: {json.dumps({'event': 'delta', 'delta': err}, ensure_ascii=False)}\n\n"

        reply = "".join(reply_parts)

        # 问答沉淀：把本轮问答对写入沉淀库（失败不影响主流程）
        try:
            save_qa(
                user_msg,
                reply,
                hit_knowledge=[r.get("视频/知识点名称", "") for r in hit_records],
                conversation_id=conv["id"],
            )
        except Exception as e:
            logger.warning("问答沉淀失败: %s", e)

        # 更新对话并落盘
        conv["messages"].append({"role": "assistant", "content": reply})
        if conv["title"] == "新对话":
            conv["title"] = _auto_title(conv["messages"])
        save_conversation(conv)

        # 结束事件
        yield f"data: {json.dumps({'event': 'done', 'conversation_id': conv['id'], 'title': conv['title']}, ensure_ascii=False)}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


# ============================================================
#  API: API Key 配置
# ============================================================
@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "POST":
        data = request.get_json(silent=True)
        if data and data.get("api_key"):
            save_api_key(data["api_key"])
            return jsonify({"ok": True})
        return jsonify({"ok": False, "msg": "API Key 不能为空"}), 400

    # GET: 返回是否有 key
    key = load_api_key()
    return jsonify({"configured": bool(key)})


# ============================================================
#  API: 获取当前分析上下文（供前端对话时预加载）
# ============================================================
@app.route("/api/context", methods=["GET"])
def api_context():
    if not _latest_agg_data:
        return jsonify({"has_data": False})
    ad = _latest_agg_data
    return jsonify({
        "has_data": True,
        "total_students": ad.get("total_students", 0),
        "weak_count": ad.get("weak_student_count", 0),
        "experiment_count": ad.get("experiment_count", 0),
        "quiz_count": ad.get("quiz_count", 0),
        "unit_count": ad.get("unit_count", 0),
        "attendance_count": ad.get("attendance_count", 0),
        "file_count": len(_latest_results),
    })


# ============================================================
#  API: 问答沉淀记录
# ============================================================
@app.route("/api/qa-sediment", methods=["GET"])
def api_qa_sediment():
    """返回问答沉淀记录（最新在前），供前端查看教学经验沉淀。"""
    logs = load_qa_logs(limit=100)
    return jsonify({"total": count_qa(), "logs": logs})


# ============================================================
#  查看完整分析报告
# ============================================================
@app.route("/view-full-report")
def view_full_report():
    if not _latest_agg_data:
        flash("暂无分析数据", "error")
        return redirect(url_for("index"))
    ad = _latest_agg_data
    success_count = sum(1 for r in _latest_results if not r.get("has_error"))
    fail_count = sum(1 for r in _latest_results if r.get("has_error"))
    return render_template("batch_report.html",
        results=_latest_results,
        success_count=success_count,
        fail_count=fail_count,
        agg_data=ad,
    )


@app.route("/files")
def data_files_page():
    """独立的数据文件管理页：搜索 + 列表 + 删除"""
    return render_template("files.html")


# ============================================================
#  以下为原有路由（保持不变）
# ============================================================

@app.route("/download/<filename>")
def download_report(filename):
    filepath = _safe_upload_path(filename)
    if filepath and filepath.exists():
        return send_file(
            str(filepath),
            as_attachment=True,
            download_name=Path(filename).name,
            mimetype="text/plain; charset=utf-8",
        )
    flash("报告文件不存在", "error")
    return redirect(url_for("index"))


@app.route("/view/<filename>")
def view_batch_report(filename):
    """从批量结果页查看单个报告"""
    filepath = _safe_upload_path(filename)
    if not filepath or not filepath.exists():
        flash("报告文件不存在", "error")
        return redirect(url_for("index"))
    report_content = filepath.read_text(encoding="utf-8")
    stem = Path(filename).stem.replace("报告_", "").replace("_", " ")
    return render_template(
        "student_report.html",
        report_title=f"分析报告：{stem}",
        report_content=report_content,
        report_filename=filename,
    )


@app.route("/single-report/<report_stem>")
def view_single_report(report_stem):
    """从 report_stem 重新分析 CSV，渲染完整的单文件分析页面"""
    csv_path = None
    # 优先从 reports 子目录找 CSV 副本，找不到再回主目录（兼容历史布局）
    for base in (REPORT_DIR, UPLOAD_DIR):
        for f in base.glob("*.csv"):
            if report_stem in f.stem:
                csv_path = f
                break
        if csv_path:
            break
    if not csv_path:
        flash("找不到对应的数据文件", "error")
        return redirect(url_for("index"))

    kb = load_knowledge_base(str(KNOWLEDGE_CSV)) if KNOWLEDGE_CSV.exists() else []
    result = analyze_touge_file(str(csv_path), kb)
    report = generate_touge_report(result)
    report_title = result.get("experiment_name", report_stem)

    sa = analyze_all_students(result, kb)
    student_list = [{"name": sr["name"], "student_id": sr.get("student_id", ""),
                      "weak_subtask_count": sr["weak_subtask_count"],
                      "weak_knowledge_count": sr["weak_knowledge_count"],
                      "weakness_rate": sr["weakness_rate"],
                      "_key": make_student_key(sr["name"], sr.get("student_id", ""))}
                     for sr in sa.get("student_results", [])]

    return render_template("report.html",
        report_title=report_title,
        report_content=report,
        source_type="touge",
        report_filename=f"报告_{report_stem}.txt",
        stats=get_stats_summary(result, "touge"),
        student_list=student_list,
        top_error=[{"name": n, "error_count": i["error_count"], "error_rate": i["error_rate"],
                     "unit": i.get("unit", "")} for n, i in sa.get("top_error_knowledge", [])],
        report_stem=report_stem,
        summary_filename=f"汇总_{report_stem}.txt",
        weak_student_count=sa.get("weak_student_count", 0))


@app.route("/knowledge-upload", methods=["POST"])
def knowledge_upload():
    """上传 PDF/Word 知识点文档,提取标题行并增量合并进知识库"""
    global knowledge_base

    if "file" not in request.files:
        flash("请选择要上传的知识点文档", "error")
        return redirect(url_for("index"))

    file = request.files["file"]
    if file.filename == "":
        flash("请选择要上传的知识点文档", "error")
        return redirect(url_for("index"))

    ext = Path(file.filename).suffix.lower()
    if ext not in {".pdf", ".docx"}:
        flash("知识点文档仅支持 PDF 或 Word(.docx)", "error")
        return redirect(url_for("index"))

    original_name = file.filename
    safe_name = secure_filename(original_name) or f"knowledge_{int(time.time())}"
    store_path = TEMP_DIR / safe_name
    file.save(str(store_path))

    try:
        added, skipped, names = import_knowledge_file(
            store_path, source=f"文档导入:{Path(original_name).stem}"
        )
        if names:
            knowledge_base = load_knowledge_base(str(KNOWLEDGE_CSV))
        if added:
            flash(
                f"知识点导入完成：新增 {added} 条，跳过重复 {skipped} 条，"
                f"知识库现有 {len(knowledge_base)} 条",
                "success",
            )
        else:
            flash(
                f"没有新增知识点：共提取到 {len(names)} 条，"
                f"全部与现有知识库重复（或文档无可提取的编号标题）",
                "warning",
            )
        return redirect(url_for("index"))
    except Exception as e:
        logger.exception("知识点文档导入失败")
        flash(f"知识点文档导入失败：{e}", "error")
        return redirect(url_for("index"))


@app.route("/analyze-questions", methods=["GET", "POST"])
def analyze_questions():
    if request.method == "GET":
        return redirect(url_for("index"))

    if "file" not in request.files:
        flash("请选择要上传的 Word 文件", "error")
        return redirect(url_for("index"))

    file = request.files["file"]
    if file.filename == "":
        flash("请选择要上传的 Word 文件", "error")
        return redirect(url_for("index"))

    if not file.filename.lower().endswith(".docx"):
        flash("仅支持 .docx 格式的 Word 文件", "error")
        return redirect(url_for("index"))

    original_name = file.filename
    safe_name = secure_filename(original_name)
    store_path = TEMP_DIR / safe_name
    file.save(str(store_path))

    try:
        questions = parse_word_questions(str(store_path))
        if not questions:
            flash("未能从 Word 文档中解析出题目，请检查文档格式", "error")
            return redirect(url_for("index"))

        matched = match_questions_to_knowledge(questions, knowledge_base)
        report = generate_question_report(matched)

        matched_count = sum(1 for m in matched if m.get("knowledge"))
        unmatched_count = sum(1 for m in matched if not m.get("knowledge"))

        report_stem = "".join(
            c if c.isalnum() or c in " -_" else "_"
            for c in Path(original_name).stem
        )
        report_filename = f"题目报告_{report_stem}.txt"
        report_path = REPORT_DIR / report_filename
        report_path.write_text(report, encoding="utf-8")

        return render_template(
            "question_report.html",
            report_title=f"题目解析：{original_name}",
            report_content=report,
            report_filename=report_filename,
            total_questions=len(matched),
            matched_count=matched_count,
            unmatched_count=unmatched_count,
        )
    except Exception as e:
        flash(f"题目解析失败：{str(e)}", "error")
        return redirect(url_for("index"))
    finally:
        store_path.unlink(missing_ok=True)


@app.route("/student-report/<report_stem>/<student_key>")
def view_student_report(report_stem, student_key):
    """查看单个学生报告"""
    filename = f"个人_{report_stem}_{student_key}.txt"
    filepath = _safe_upload_path(filename)
    if not filepath or not filepath.exists():
        flash("学生报告不存在", "error")
        return redirect(url_for("index"))
    report_content = filepath.read_text(encoding="utf-8")
    return render_template(
        "student_report.html",
        report_title=f"个人报告",
        report_content=report_content,
        report_filename=filename,
    )


@app.route("/download-student-report/<report_stem>/<student_key>")
def download_student_report(report_stem, student_key):
    """下载单个学生报告"""
    filename = f"个人_{report_stem}_{student_key}.txt"
    filepath = _safe_upload_path(filename)
    if filepath and filepath.exists():
        return send_file(
            str(filepath),
            as_attachment=True,
            download_name=Path(filename).name,
            mimetype="text/plain; charset=utf-8",
        )
    flash("学生报告不存在", "error")
    return redirect(url_for("index"))


@app.route("/student-report/<student_key>")
def view_student_aggregate(student_key):
    """查看单个学生的跨实验综合报告"""
    filename = f"学生_{student_key}.txt"
    filepath = _safe_upload_path(filename)
    if not filepath or not filepath.exists():
        flash("学生报告不存在", "error")
        return redirect(url_for("index"))
    report_content = filepath.read_text(encoding="utf-8")
    return render_template(
        "student_report.html",
        report_title=f"学生报告",
        report_content=report_content,
        report_filename=filename,
    )


@app.route("/download-student-report/<student_key>")
def download_student_aggregate(student_key):
    """下载单个学生的跨实验综合报告"""
    filename = f"学生_{student_key}.txt"
    filepath = _safe_upload_path(filename)
    if filepath and filepath.exists():
        return send_file(
            str(filepath), as_attachment=True,
            download_name=Path(filename).name, mimetype="text/plain; charset=utf-8",
        )
    flash("学生报告不存在", "error")
    return redirect(url_for("index"))


def get_stats_summary(result, source_type):
    if source_type == "touge":
        return [
            {"label": "学生人数", "value": result.get("student_count", 0)},
            {"label": "平均分", "value": result.get("avg_final_score", 0)},
            {"label": "最低分", "value": result.get("min_final_score", 0)},
            {"label": "最高分", "value": result.get("max_final_score", 0)},
            {"label": "低分人数", "value": result.get("low_score_count", 0)},
            {"label": "子任务数", "value": len(result.get("task_stats", {}))},
        ]
    else:
        ld = result.get("level_distribution", {})
        weak = result.get("weak_assessments", {})
        weak_list = [s["name"] for s in weak.values()]
        return [
            {"label": "有考核数据", "value": result.get("valid_count", 0)},
            {"label": "合格", "value": ld.get("合格", 0)},
            {"label": "不合格", "value": result.get("fail_count", 0)},
            {"label": "短板", "value": "、".join(weak_list) if weak_list else "无"},
        ]


if __name__ == "__main__":
    # 启动时自动恢复 uploads 目录中已上传的数据（并重建成绩预测）
    _restore_uploads_on_startup()
    # 报告归档治理：清理超限的最旧 txt 报告
    _trim_report_archives()
    # 知识库自动备份：保留最近 20 份带时间戳副本，防人工误操作覆盖
    _backup_knowledge_base()
    print("=" * 50)
    print("  教学预警系统 Web 界面已启动")
    print(f"  访问地址：http://localhost:5000")
    print("=" * 50)
    try:
        # 生产级服务器：waitress 纯 Python 实现，多线程并发，Windows 友好
        from waitress import serve
        logger.info("使用 waitress 生产级服务器启动（threads=8）")
        serve(app, host="0.0.0.0", port=5000, threads=8)
    except ImportError:
        # 未安装 waitress 时回退 Flask 开发服务器
        logger.warning("未安装 waitress，回退 Flask 开发服务器（建议 pip install waitress）")
        app.run(debug=False, threaded=True, host="0.0.0.0", port=5000)
