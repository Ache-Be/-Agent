# _*_ coding : UTF-8 _*_
"""
报告中心 API：报告列表 / 报告预览 / 单文件报告 / 学生报告 / 下载
补齐原 Flask 中「view-full-report / single-report / student-report / download-*」
"""
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse

from core import state
from core.config import config
from core.logging_setup import logger
from core.utils import make_student_key, safe_upload_path, fmt_file_size, file_md5
from services.analysis_service import rebuild_agg_data

router = APIRouter(prefix="/reports", tags=["报告中心"])


def _mod():
    """懒加载 analysis 模块"""
    from analysis.reporter import generate_touge_report
    from analysis.analyzer import analyze_touge_file
    from analysis.student_analyzer import analyze_all_students, generate_student_report, generate_class_summary
    return {
        "analyze_touge_file": analyze_touge_file,
        "generate_touge_report": generate_touge_report,
        "analyze_all_students": analyze_all_students,
        "generate_student_report": generate_student_report,
        "generate_class_summary": generate_class_summary,
    }


def _stats_summary(result, source_type: str) -> List[Dict[str, Any]]:
    if source_type == "touge":
        return [
            {"label": "学生人数", "value": result.get("student_count", 0)},
            {"label": "平均分", "value": result.get("avg_final_score", 0)},
            {"label": "最低分", "value": result.get("min_final_score", 0)},
            {"label": "最高分", "value": result.get("max_final_score", 0)},
            {"label": "低分人数", "value": result.get("low_score_count", 0)},
            {"label": "子任务数", "value": len(result.get("task_stats", {}))},
        ]
    ld = result.get("level_distribution", {})
    weak = result.get("weak_assessments", {})
    weak_list = [s["name"] for s in weak.values()]
    return [
        {"label": "有考核数据", "value": result.get("valid_count", 0)},
        {"label": "合格", "value": ld.get("合格", 0)},
        {"label": "不合格", "value": result.get("fail_count", 0)},
        {"label": "短板", "value": "、".join(weak_list) if weak_list else "无"},
    ]


# ---------- 报告总览列表 ----------
import re as _re
import time as _time

# 姓名：中文2~8个连续汉字
_NAME_RE = _re.compile(r"[\u4e00-\u9fa5]{2,8}")
# 学号：允许 2~20 位数字，中间可能夹杂下划线（处理头歌等平台导出的混乱 ID）
_SID_RE = _re.compile(r"\d[\d_]{1,19}\d")
# 姓名+学号紧邻模式：[姓名] [零或多个 _ / - / 空格] [学号]
_NAME_SID_NEAR_RE = _re.compile(
    r"([\u4e00-\u9fa5]{2,8})[\s_\-]{0,15}(\d[\d_]{1,19}\d)"
)
# 学号+姓名紧邻模式（学号在前，姓名在后）：[学号] [零或多个 _ / - / 空格] [姓名]
_SID_NAME_NEAR_RE = _re.compile(
    r"(\d[\d_]{1,19}\d)[\s_\-]{0,15}([\u4e00-\u9fa5]{2,8})"
)
# 非姓名的 2-6 字中文词黑名单（文件名常见前缀/技术术语，绝不可能是学生姓名）
_NAME_BLACKLIST = frozenset([
    "工具", "工具类", "词频", "词频统计", "统计", "分析", "汇总", "报告", "实验",
    "试题", "题目", "作业", "练习", "单元", "课堂", "测验", "班级", "年级", "成绩",
    "数据", "答案", "解析", "提交", "批阅", "草稿", "临时", "备份", "复制", "新建",
    "示例", "模板", "测试", "导出", "导入", "同步", "更新", "期末", "期中", "补考",
    "重修", "阶段", "知识", "知识点", "预习", "复习", "作业", "Java", "答案",
    "程序", "设计", "编程", "基础", "进阶", "综合",
])

# 报告列表唯一全量缓存（30s TTL）：所有 type 都先取全量再内存过滤，不再多次扫盘
# key 固定 "all"，value = (expire_at, all_items_list, total_count)
_LIST_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]], int]] = {}


def _is_real_name(candidate: str) -> bool:
    """中文姓名可信度判断：黑名单排除 + 2~4 字（真实中文名几乎都在 2-4 字）"""
    if not candidate:
        return False
    if len(candidate) < 2 or len(candidate) > 4:
        return False
    if candidate in _NAME_BLACKLIST:
        return False
    return True


def _extract_student_display(filename_stem: str) -> Optional[str]:
    """
    从任意格式的报告文件名里提取「姓名_学号」，找不到返回 None。
    严格匹配规则（避免"工具类_xxx"误判）：
      1. 优先 姓名+学号紧邻 或 学号+姓名紧邻（中间只能 0-1 个 _ / - / 空格）。
      2. 姓名必须真实可信（2-4 字 + 不在中文黑名单）。
      3. 若文件名有明确前缀「学生_」/「个人_」，放宽到第二段+第三段匹配。
    兼容：
      学生_陆俊成_252219605316.txt              → 陆俊成_252219605316
      个人_Java工具类4词频统计_陆俊成_252219605316.txt → 陆俊成_252219605316
      冯佳乐_252219702509_试题3.txt            → 冯佳乐_252219702509
      试题4-冯佳乐-252219702509.txt            → 冯佳乐_252219702509
      工具类_3531862_xxx.txt                   → None（"工具类"黑名单）
    """
    stem = filename_stem
    # 先去掉最外层前缀：学生_ / 个人_ / 汇总_ / 报告_ / 题目报告_（不影响后续匹配）
    stripped = _re.sub(r"^(学生_|个人_|汇总_|报告_|题目报告_)", "", stem)

    # 模式 1：姓名在前 + 学号紧邻
    m1 = _NAME_SID_NEAR_RE.search(stripped)
    if m1:
        name, sid = m1.group(1), m1.group(2)
        if _is_real_name(name):
            return f"{name}_{sid}"
    # 模式 2：学号在前 + 姓名紧邻
    m2 = _SID_NAME_NEAR_RE.search(stripped)
    if m2:
        sid, name = m2.group(1), m2.group(2)
        if _is_real_name(name):
            return f"{name}_{sid}"
    # 模式 3：原 stem 里也再搜一遍（防止前缀里有匹配）
    m1b = _NAME_SID_NEAR_RE.search(stem)
    if m1b:
        name, sid = m1b.group(1), m1b.group(2)
        if _is_real_name(name):
            return f"{name}_{sid}"
    m2b = _SID_NAME_NEAR_RE.search(stem)
    if m2b:
        sid, name = m2b.group(1), m2b.group(2)
        if _is_real_name(name):
            return f"{name}_{sid}"
    return None


def _friendly_display(name: str) -> str:
    """给前端显示用的友好名称：学生类 → 姓名_学号；其他类 → 去前缀保留实验名"""
    stem = Path(name).stem
    student_name = _extract_student_display(stem)
    if student_name:
        return student_name
    # 非学生类：去前缀（学生_/个人_/汇总_/报告_/题目报告_）
    return _re.sub(r"^(学生_|个人_|汇总_|报告_|题目报告_)", "", stem) or stem


@router.get("")
async def api_list_reports(type: str = "all"):
    """
    报告列表：
    - type=summary  →  汇总 / 综合报告
    - type=student  →  学生个人报告
    - type=single   →  单文件报告（报告_xxx）
    - type=question →  题目报告
    - type=all      →  全部
    返回字段里新增：display_name（给用户看的名字）、student_name / student_id（若能提取）
    性能：全量列表 30s 内存级单 entry 缓存，所有 type 都先取缓存再内存过滤，零重复扫盘
    """
    report_dir = config.report_dir

    # 1. 取全量 30s 缓存（所有 type 共享，切 tab 零扫盘）
    now_ts = _time.time()
    cached = _LIST_CACHE.get("all")
    all_items: List[Dict[str, Any]] = []
    if cached and cached[0] > now_ts and report_dir.exists():
        all_items = cached[1]
    else:
        def _classify(name: str) -> str:
            if name.startswith("汇总_") or name.startswith("学生综合汇总"):
                return "summary"
            if name.startswith("个人_"):
                return "student"
            if name.startswith("题目报告_"):
                return "question"
            # 前缀强约束：学生_ 直接算（最可信），其他必须靠严格的 _extract_student_display 提取
            if name.startswith("学生_"):
                return "student"
            if name.startswith("报告_"):
                return "single"
            # 按内容严格判断：必须满足姓名黑名单+紧邻+长度校验
            if _extract_student_display(Path(name).stem):
                return "student"
            return "other"

        entries: List[Tuple[float, Dict[str, Any]]] = []
        if report_dir.exists():
            for de in report_dir.iterdir():
                try:
                    if not de.is_file() or de.suffix.lower() != ".txt":
                        continue
                    name = de.name
                    category = _classify(name)
                    st = de.stat(follow_symlinks=False)
                    stem = de.stem
                    sname = _extract_student_display(stem)
                    student_name = student_id = ""
                    if sname and "_" in sname:
                        student_name, student_id = sname.split("_", 1)
                    item = {
                        "name": name,
                        "display_name": _friendly_display(name),
                        "category": category,
                        "student_name": student_name,
                        "student_id": student_id,
                        "size": st.st_size,
                        "size_str": fmt_file_size(st.st_size),
                        "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    }
                    entries.append((st.st_mtime, item))
                except OSError:
                    continue
        entries.sort(key=lambda x: -x[0])
        all_items = [x[1] for x in entries]
        # 30s 单 entry 缓存（不分 type）
        _LIST_CACHE["all"] = (now_ts + 30.0, all_items, len(all_items))

    # 2. 内存按 type 过滤（毫秒级，不再扫盘）
    if type == "all":
        items = all_items
    elif type == "student":
        # 学生类去重逻辑：按 student_id (或 name) 分组，每个学生仅保留一个最权威报告
        # 优先级：学生_ (跨实验综合) > 个人_ (单次实验)；同类则取最新 (mtime)
        student_groups: Dict[str, List[Dict[str, Any]]] = {}
        for it in all_items:
            if it.get("category") != "student":
                continue
            # 唯一标识：优先用学号，没学号用姓名
            sid = it.get("student_id")
            sname = it.get("student_name")
            key = sid if sid else sname
            if not key:
                continue
            if key not in student_groups:
                student_groups[key] = []
            student_groups[key].append(it)

        deduped_items = []
        for key, group in student_groups.items():
            # 排序：综合报告排前面，其次按时间倒序
            group.sort(key=lambda x: (
                1 if x["name"].startswith("学生_") else 0,
                x.get("mtime", "")
            ), reverse=True)
            # 取最权威的一个
            best = dict(group[0])  # 使用副本，避免修改缓存
            # 如果有多个报告，可以在 display_name 里提示一下（可选）
            if len(group) > 1:
                best["display_name"] = f"{best['display_name']} ({len(group)}份报告)"
            deduped_items.append(best)
        items = deduped_items
    else:
        items = [it for it in all_items if it.get("category") == type]
    
    return {"total": len(items), "items": items}


# ---------- 预览报告内容 ----------
@router.get("/content/{filename:path}")
async def api_view_report(filename: str):
    """返回报告文本内容，前端自行按 Markdown/纯文本渲染"""
    fp = safe_upload_path(filename)
    if not fp:
        raise HTTPException(404, "报告文件不存在")
    try:
        content = fp.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        raise HTTPException(500, f"读取失败：{e}")
    return {
        "name": fp.name,
        "size": fp.stat().st_size,
        "mtime": datetime.fromtimestamp(fp.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        "content": content,
    }


# ---------- 下载报告 ----------
@router.get("/download/{filename:path}")
async def api_download_report(filename: str):
    fp = safe_upload_path(filename)
    if not fp:
        raise HTTPException(404, "报告文件不存在")
    media = "text/plain; charset=utf-8" if fp.suffix.lower() == ".txt" else "application/octet-stream"
    return FileResponse(str(fp), filename=fp.name, media_type=media)


# ---------- 批量分析总览（仪表盘 → 完整批量报告页）----------
@router.get("/overview")
async def api_report_overview():
    """返回报告详情页（批量总览）所需要的全部结构化数据"""
    ad = state.latest_agg_data
    if not ad:
        return {"has_data": False}

    success_count = sum(1 for r in state.latest_results if not r.get("has_error"))
    fail_count = sum(1 for r in state.latest_results if r.get("has_error"))

    student_list = ad.get("student_list", [])
    weak_students = [s for s in student_list if s.get("weak_count", 0) > 0]

    return {
        "has_data": True,
        "results": state.latest_results,
        "success_count": success_count,
        "fail_count": fail_count,
        "agg": {
            "total_students": ad.get("total_students", 0),
            "weak_student_count": ad.get("weak_student_count", 0),
            "experiment_count": ad.get("experiment_count", 0),
            "quiz_count": ad.get("quiz_count", 0),
            "unit_count": ad.get("unit_count", 0),
            "attendance_count": ad.get("attendance_count", 0),
            "student_count": len(student_list),
            "weak_student_count_live": len(weak_students),
            "top_error": ad.get("top_error", []),
        },
        "student_list": [
            {
                "name": s.get("name", ""),
                "student_id": s.get("student_id", ""),
                "_key": s.get("_key", make_student_key(s.get("name", ""), s.get("student_id", ""))),
                "weak_subtask_count": s.get("weak_subtask_count", 0),
                "weak_knowledge_count": s.get("weak_knowledge_count", 0),
                "weakness_rate": s.get("weakness_rate", 0),
                "weak_count": s.get("weak_count", 0),
            }
            for s in student_list[:200]
        ],
    }


# ---------- 单文件重新分析报告（原 single-report/<report_stem>）----------
@router.get("/single/{report_stem}")
async def api_single_file_report(report_stem: str):
    """根据 report_stem 找 CSV，重新分析头歌文件，返回结构化结果"""
    csv_path: Optional[Path] = None
    for base in (config.report_dir, config.upload_dir):
        if not base.exists():
            continue
        for f in base.glob("*.csv"):
            if report_stem in f.stem:
                csv_path = f
                break
        if csv_path:
            break
    if not csv_path:
        raise HTTPException(404, "找不到对应的数据文件")
    mod = _mod()
    kb = state.knowledge_base or []
    result = mod["analyze_touge_file"](str(csv_path), kb)
    report = mod["generate_touge_report"](result)
    sa = mod["analyze_all_students"](result, kb)
    student_list = [
        {
            "name": s["name"],
            "student_id": s.get("student_id", ""),
            "weak_subtask_count": s["weak_subtask_count"],
            "weak_knowledge_count": s["weak_knowledge_count"],
            "weakness_rate": s["weakness_rate"],
            "_key": make_student_key(s["name"], s.get("student_id", "")),
        }
        for s in sa.get("student_results", [])
    ]
    top_error = [
        {"name": n, "error_count": i["error_count"],
         "error_rate": i["error_rate"], "unit": i.get("unit", "")}
        for n, i in sa.get("top_error_knowledge", [])
    ]
    # 同时写入报告文件
    report_filename = f"报告_{report_stem}.txt"
    try:
        (config.report_dir / report_filename).write_text(report, encoding="utf-8")
    except Exception:
        pass
    return {
        "report_title": result.get("experiment_name", report_stem),
        "report_filename": report_filename,
        "stats": _stats_summary(result, "touge"),
        "student_list": student_list,
        "top_error": top_error,
        "report_stem": report_stem,
        "summary_filename": f"汇总_{report_stem}.txt",
        "weak_student_count": sa.get("weak_student_count", 0),
        "report_content": report,
    }


# ---------- 学生个人报告 ----------
@router.get("/student/{report_stem}/{student_key}")
@router.get("/student/{student_key}")
async def api_student_report(student_key: str, report_stem: Optional[str] = None):
    """
    两种：
      GET /student/{report_stem}/{student_key}   —— 学生在某次实验中的个人报告
      GET /student/{student_key}                 —— 学生综合报告（跨实验）
    """
    if report_stem:
        filename = f"个人_{report_stem}_{student_key}.txt"
    else:
        filename = f"学生_{student_key}.txt"
    fp = safe_upload_path(filename)
    if not fp:
        raise HTTPException(404, "学生报告不存在")
    content = fp.read_text(encoding="utf-8", errors="ignore")
    return {
        "report_title": "学生综合报告" if not report_stem else f"{report_stem} 个人报告",
        "report_filename": filename,
        "report_content": content,
    }


# ---------- 题目分析（Word） ----------
@router.post("/analyze-questions")
async def api_analyze_questions(file: UploadFile = File(...)):
    """上传 Word(.docx) 题目文档，匹配知识点生成题目报告"""
    from analysis.question_parser import (
        parse_word_questions,
        match_questions_to_knowledge,
        generate_question_report,
    )
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(400, "仅支持 .docx 格式的 Word 文件")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "空文件")
    original_name = Path(file.filename).name
    temp_path = config.temp_dir / original_name
    temp_path.write_bytes(raw)
    try:
        questions = parse_word_questions(str(temp_path))
        if not questions:
            raise HTTPException(400, "未能从 Word 文档中解析出题目，请检查文档格式")
        matched = match_questions_to_knowledge(questions, state.knowledge_base or [])
        report = generate_question_report(matched)
        matched_count = sum(1 for m in matched if m.get("knowledge"))
        unmatched_count = sum(1 for m in matched if not m.get("knowledge"))
        stem = "".join(c if c.isalnum() or c in " -_" else "_"
                       for c in Path(original_name).stem)
        report_fn = f"题目报告_{stem}.txt"
        (config.report_dir / report_fn).write_text(report, encoding="utf-8")
        return {
            "report_title": f"题目解析：{original_name}",
            "report_filename": report_fn,
            "report_content": report,
            "total_questions": len(matched),
            "matched_count": matched_count,
            "unmatched_count": unmatched_count,
            "matched_details": [
                {"idx": i, "question": q.get("question", "")[:60],
                 "knowledge": (q.get("knowledge") or {}).get("视频/知识点名称", "")
                 if isinstance(q.get("knowledge"), dict) else ""}
                for i, q in enumerate(matched[:50], 1)
            ],
        }
    finally:
        temp_path.unlink(missing_ok=True)


# ---------- 知识点文档导入（PDF/Word） ----------
@router.post("/knowledge-import")
async def api_import_knowledge(file: UploadFile = File(...)):
    """上传 PDF / Word 知识点文档，增量合并进知识库 CSV"""
    from analysis.knowledge_builder import load_knowledge_base
    from analysis.knowledge_importer import import_knowledge_file

    global state
    if not file.filename:
        raise HTTPException(400, "请选择要上传的知识点文档")
    ext = Path(file.filename).suffix.lower()
    if ext not in (".pdf", ".docx"):
        raise HTTPException(400, "知识点文档仅支持 PDF 或 Word(.docx)")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "空文件")
    temp_path = config.temp_dir / Path(file.filename).name
    temp_path.write_bytes(raw)
    try:
        added, skipped, names = import_knowledge_file(
            str(temp_path), source=f"文档导入:{Path(file.filename).stem}"
        )
        if names:
            state.knowledge_base = load_knowledge_base(str(config.knowledge_csv))
        from core.utils import backup_knowledge_base
        try:
            backup_knowledge_base()
        except Exception as e:
            logger.warning("知识库导入后备份失败: %s", e)
        total = len(state.knowledge_base or [])
        message = (f"知识点导入完成：新增 {added} 条，跳过重复 {skipped} 条，知识库现有 {total} 条"
                   if added else
                   f"没有新增知识点：共提取到 {len(names)} 条，全部与现有知识库重复")
        return {"ok": added > 0, "added": added, "skipped": skipped,
                "total": total, "message": message}
    except Exception as e:
        logger.exception("知识点文档导入失败")
        raise HTTPException(500, f"导入失败：{e}")
    finally:
        temp_path.unlink(missing_ok=True)
