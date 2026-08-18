# _*_ coding : UTF-8 _*_
"""
分析服务：原 web/app.py 中「上传解析 + 聚合重建 + 启动恢复 + 快照」相关逻辑
直接复用 analysis/* 模块原有函数，不变更业务逻辑
"""
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core import state
from core.config import config
from core.logging_setup import logger
from core.utils import (
    allowed_file,
    file_md5,
    fmt_file_size,
    looks_like_utf8,
    make_student_key,
    safe_upload_path,
)

# -------- analysis 模块导入（按需要懒加载，避免启动慢）--------
def _analysis_modules():
    from analysis.knowledge_builder import load_knowledge_base  # noqa: F401
    from analysis.knowledge_importer import import_knowledge_file  # noqa: F401
    from analysis.analyzer import analyze_touge_file, analyze_mooc_file, detect_data_source
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
    from analysis.extra_parser import (
        analyze_unit_file,
        analyze_attendance_file,
        analyze_knowledge_file,
    )
    from analysis.predictor import build_prediction_text

    return {
        "analyze_touge_file": analyze_touge_file,
        "analyze_mooc_file": analyze_mooc_file,
        "detect_data_source": detect_data_source,
        "generate_touge_report": generate_touge_report,
        "generate_mooc_report": generate_mooc_report,
        "analyze_quiz_file": analyze_quiz_file,
        "analyze_unit_file": analyze_unit_file,
        "analyze_attendance_file": analyze_attendance_file,
        "analyze_knowledge_file": analyze_knowledge_file,
        "analyze_all_students": analyze_all_students,
        "generate_student_report": generate_student_report,
        "generate_class_summary": generate_class_summary,
        "aggregate_students": aggregate_students,
        "generate_cross_student_report": generate_cross_student_report,
        "generate_cross_summary": generate_cross_summary,
        "build_prediction_text": build_prediction_text,
    }


SNAPSHOT_FILE = config.log_dir.parent / "config" / "analysis_snapshot.json"
SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)


# -------- 数据文件列表 --------
def list_data_files() -> List[Dict[str, Any]]:
    files: List[Dict[str, Any]] = []
    upload_dir = config.upload_dir
    if upload_dir.exists():
        for f in sorted(upload_dir.iterdir(), key=lambda x: x.name.lower()):
            if f.is_file() and f.suffix.lower() in (".csv", ".xlsx"):
                try:
                    st = f.stat()
                    files.append({
                        "name": f.name,
                        "size": st.st_size,
                        "size_str": fmt_file_size(st.st_size),
                        "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    })
                except OSError:
                    continue
    return files


# -------- 聚合分析重建 --------
def rebuild_agg_data():
    """根据当前 state 中 _latest_* 结果重新生成聚合分析与学生成绩预测。"""
    mod = _analysis_modules()
    if not (state.latest_experiment_results or state.latest_quiz_results
            or state.latest_unit_results or state.latest_attendance_results):
        state.latest_agg_data = None
        _save_analysis_snapshot()
        return None

    agg = mod["aggregate_students"](
        state.latest_experiment_results, state.knowledge_base,
        quiz_results=state.latest_quiz_results
    )
    agg_report = mod["generate_cross_summary"](agg)
    agg_filename = "学生综合汇总报告.txt"
    (config.report_dir / agg_filename).write_text(agg_report, encoding="utf-8")

    for sid, si in agg["students"].items():
        srep = mod["generate_cross_student_report"](si)
        safe_key = make_student_key(si.get("name", ""), sid)
        (config.report_dir / f"学生_{safe_key}.txt").write_text(srep, encoding="utf-8")

    student_list = []
    for s in agg["student_list"]:
        s["_key"] = make_student_key(s["name"], s["student_id"])
        student_list.append(s)
    top_error = [
        {"name": n, "error_count": i["error_count"],
         "error_rate": i["error_rate"], "unit": i.get("unit", "")}
        for n, i in agg.get("top_error_knowledge", [])
    ]
    weak_count = sum(1 for s in student_list if s.get("weak_count", 0) > 0)
    eff_total = agg["total_students"] or max(
        sum(u.get("student_count", 0) for u in state.latest_unit_results),
        sum(a.get("student_count", 0) for a in state.latest_attendance_results),
        0,
    )

    state.latest_agg_data = {
        "student_list": student_list,
        "top_error": top_error,
        "total_students": eff_total,
        "weak_student_count": weak_count,
        "experiment_count": agg["experiment_count"],
        "quiz_count": agg.get("quiz_count", 0),
        "unit_count": len(state.latest_unit_results),
        "attendance_count": len(state.latest_attendance_results),
        "agg_filename": agg_filename,
        "students": agg.get("students", {}),
        # 整体名录（供 AI 教学助手按"年份/班级"检索命中）
        "all_class_names": agg.get("all_class_names", []),
        "all_detected_years": agg.get("all_detected_years", []),
        "all_experiment_names": agg.get("all_experiment_names", []),
    }
    try:
        state.latest_agg_data["prediction_text"] = mod["build_prediction_text"](
            state.latest_agg_data["students"],
            unit_results=state.latest_unit_results,
            attendance_results=state.latest_attendance_results,
        )
    except Exception as e:
        logger.warning("生成成绩预测失败：%s", e)
        state.latest_agg_data["prediction_text"] = ""
    _save_analysis_snapshot()
    return agg


# -------- 快照 --------
def _current_upload_fingerprint() -> Dict[str, str]:
    fp: Dict[str, str] = {}
    if config.upload_dir.exists():
        for f in config.upload_dir.iterdir():
            if f.is_file() and f.suffix.lower() in (".csv", ".xlsx"):
                try:
                    fp[f.name] = file_md5(f)
                except Exception:
                    fp[f.name] = "?"
    return fp


def _save_analysis_snapshot():
    try:
        snapshot = {
            "fingerprint": _current_upload_fingerprint(),
            "latest_results": state.latest_results,
            "agg_data": state.latest_agg_data,
            "experiment_results": state.latest_experiment_results,
            "quiz_results": state.latest_quiz_results,
            "unit_results": state.latest_unit_results,
            "attendance_results": state.latest_attendance_results,
            "knowledge_results": state.latest_knowledge_results,
        }
        SNAPSHOT_FILE.write_text(
            json.dumps(snapshot, ensure_ascii=False, default=str), encoding="utf-8"
        )
    except Exception as e:
        logger.warning("保存分析快照失败：%s", e)


def _try_restore_snapshot() -> bool:
    if not SNAPSHOT_FILE.exists():
        return False
    try:
        snap = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
        if snap.get("fingerprint") != _current_upload_fingerprint():
            logger.info("快照与当前上传文件不一致，跳过快照恢复")
            return False
        state.latest_results = snap.get("latest_results", [])
        state.latest_agg_data = snap.get("agg_data")
        state.latest_experiment_results = snap.get("experiment_results", [])
        state.latest_quiz_results = snap.get("quiz_results", [])
        state.latest_unit_results = snap.get("unit_results", [])
        state.latest_attendance_results = snap.get("attendance_results", [])
        state.latest_knowledge_results = snap.get("knowledge_results", [])
        logger.info("启动恢复：使用分析快照（%d 个文件，免重新解析）",
                    len(snap.get("fingerprint", {})))
        return True
    except Exception as e:
        logger.warning("读取分析快照失败：%s", e)
        return False


# -------- 启动恢复 --------
def restore_uploads_on_startup():
    """服务启动时自动恢复 uploads 目录中已上传的分析文件"""
    # 重建文件内容哈希索引
    state.file_hashes.clear()
    if config.upload_dir.exists():
        for f in config.upload_dir.iterdir():
            if f.is_file() and f.suffix.lower() in (".csv", ".xlsx"):
                try:
                    state.file_hashes[file_md5(f)] = f.name
                except Exception:
                    pass

    if _try_restore_snapshot():
        return

    if not config.upload_dir.exists():
        return
    skip_prefixes = ("汇总_", "个人_", "学生_", "报告_")
    data_files = sorted(
        f for f in config.upload_dir.iterdir()
        if f.is_file() and f.suffix.lower() in (".csv", ".xlsx")
        and not f.name.startswith(skip_prefixes)
    )
    if not data_files:
        return
    logger.info("启动恢复：发现 %d 个数据文件", len(data_files))
    mod = _analysis_modules()

    experiment_results, quiz_results, unit_results = [], [], []
    attendance_results, knowledge_results = [], []
    restored_entries = []

    temp_dir = config.temp_dir
    for f in data_files:
        original_name = f.name
        temp_path = temp_dir / original_name
        try:
            shutil.copy2(str(f), str(temp_path))
        except Exception:
            continue
        entry = {"original_name": original_name, "source_type": "",
                 "experiment_name": original_name, "report_filename": "",
                 "student_count": 0, "weak_count": 0, "has_error": False}
        try:
            if f.suffix.lower() == ".xlsx":
                fn = original_name.lower()
                if "单元练习" in fn:
                    res = mod["analyze_unit_file"](str(temp_path))
                    res["uploaded_name"] = original_name
                    unit_results.append(res)
                    entry["source_type"] = "单元练习"
                    entry["experiment_name"] = res.get("class_name", original_name)
                    entry["student_count"] = res.get("student_count", 0)
                elif "课堂活动" in fn or "分数明细" in fn:
                    res = mod["analyze_attendance_file"](str(temp_path))
                    res["uploaded_name"] = original_name
                    attendance_results.append(res)
                    entry["source_type"] = "课堂活动"
                    entry["experiment_name"] = res.get("class_name", original_name)
                    entry["student_count"] = res.get("student_count", 0)
                else:
                    try:
                        quiz = mod["analyze_quiz_file"](str(temp_path))
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
                        for analyzer_fn, key in [
                            ("analyze_unit_file", ("单元练习",)),
                            ("analyze_attendance_file", ("课堂活动",)),
                            ("analyze_knowledge_file", ("知识点掌握度",)),
                        ]:
                            try:
                                res = mod[analyzer_fn](str(temp_path))
                                res["uploaded_name"] = original_name
                                if key[0] == "单元练习":
                                    unit_results.append(res)
                                elif key[0] == "课堂活动":
                                    attendance_results.append(res)
                                else:
                                    knowledge_results.append(res)
                                entry["source_type"] = key[0]
                                entry["experiment_name"] = res.get("class_name", original_name)
                                entry["student_count"] = res.get("student_count", 0)
                                break
                            except Exception:
                                continue
            else:
                source_type = mod["detect_data_source"](str(temp_path))
                entry["source_type"] = source_type
                if source_type in ("touge", "auto"):
                    try:
                        result = mod["analyze_touge_file"](str(temp_path), state.knowledge_base)
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

    # 合并去重
    def _merge_unique(dst: list, src: list, dedupe_key: str):
        for item in src:
            if not any(x.get(dedupe_key) == item.get(dedupe_key) for x in dst):
                dst.append(item)

    _merge_unique(state.latest_experiment_results, experiment_results, "experiment_name")
    _merge_unique(state.latest_quiz_results, quiz_results, "uploaded_name")
    _merge_unique(state.latest_unit_results, unit_results, "uploaded_name")
    _merge_unique(state.latest_attendance_results, attendance_results, "uploaded_name")
    _merge_unique(state.latest_knowledge_results, knowledge_results, "uploaded_name")

    if restored_entries:
        state.latest_results = restored_entries
    rebuild_agg_data()
    logger.info(
        "启动恢复完成：实验 %d、随堂 %d、单元 %d、课堂 %d、知识点 %d，学生 %d",
        len(state.latest_experiment_results), len(state.latest_quiz_results),
        len(state.latest_unit_results), len(state.latest_attendance_results),
        len(state.latest_knowledge_results),
        (state.latest_agg_data or {}).get("total_students", 0),
    )


# -------- 删除文件 --------
def delete_data_file(name: str) -> bool:
    """删除单个上传的数据文件"""
    return delete_batch_data_files([name])


def delete_batch_data_files(names: List[str]) -> bool:
    """批量删除上传的数据文件，连带清理相关报告和内存结果。"""
    import os as _os
    if not names:
        return True

    removed_count = 0
    removed_experiments = set()
    name_set = set(names)

    for name in names:
        target = config.upload_dir / _os.path.basename(name)
        if target.parent.resolve() != config.upload_dir.resolve() or not target.is_file():
            continue
        try:
            target.unlink()
            removed_count += 1
        except OSError:
            continue

        # 清理归档报告
        stem = "".join(c if c.isalnum() or c in " -_" else "_" for c in Path(name).stem)
        for pat in (f"报告_{stem}.txt", f"汇总_{stem}.txt", f"{stem}.csv"):
            fp = config.report_dir / pat
            try:
                if fp.exists():
                    fp.unlink()
            except OSError:
                pass
        for fp in config.report_dir.glob(f"个人_{stem}_*.txt"):
            try:
                fp.unlink()
            except OSError:
                pass

    if removed_count == 0:
        return False

    # 更新内存状态
    for e in state.latest_results:
        if e.get("original_name") in name_set and e.get("experiment_name"):
            removed_experiments.add(e["experiment_name"])

    state.file_hashes = {h: fn for h, fn in state.file_hashes.items() if fn not in name_set}
    state.latest_results = [e for e in state.latest_results if e.get("original_name") not in name_set]

    if removed_experiments:
        state.latest_experiment_results = [
            e for e in state.latest_experiment_results if e.get("experiment_name") not in removed_experiments
        ]
    state.latest_quiz_results = [e for e in state.latest_quiz_results if e.get("uploaded_name") not in name_set]
    state.latest_unit_results = [e for e in state.latest_unit_results if e.get("uploaded_name") not in name_set]
    state.latest_attendance_results = [e for e in state.latest_attendance_results if e.get("uploaded_name") not in name_set]
    state.latest_knowledge_results = [e for e in state.latest_knowledge_results if e.get("uploaded_name") not in name_set]

    if not (state.latest_experiment_results or state.latest_quiz_results
            or state.latest_unit_results or state.latest_attendance_results):
        state.latest_agg_data = None
        _save_analysis_snapshot()
    else:
        rebuild_agg_data()
    return True


def delete_all_data_files() -> bool:
    """清空所有上传的数据文件及分析结果（重置状态）"""
    import shutil
    try:
        # 物理删除 uploads 目录下的所有数据文件
        if config.upload_dir.exists():
            for f in config.upload_dir.iterdir():
                if f.is_file() and f.suffix.lower() in (".csv", ".xlsx"):
                    f.unlink()

        # 物理删除 reports 目录下的所有报告文件
        if config.report_dir.exists():
            for f in config.report_dir.iterdir():
                if f.is_file():
                    f.unlink()

        # 重置内存状态
        state.file_hashes.clear()
        state.latest_results = []
        state.latest_experiment_results = []
        state.latest_quiz_results = []
        state.latest_unit_results = []
        state.latest_attendance_results = []
        state.latest_knowledge_results = []
        state.latest_agg_data = None

        # 更新快照
        _save_analysis_snapshot()
        return True
    except Exception as e:
        logger.error("清空所有文件失败: %s", e)
        return False


# -------- 文件下载/预览 --------
def get_file_for_download(filename: str) -> Optional[Path]:
    return safe_upload_path(filename)
