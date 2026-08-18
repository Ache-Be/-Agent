# _*_ coding : UTF-8 _*_
"""
上传分析服务：处理 multipart 文件上传、调用分析逻辑并生成报告
"""
import hashlib
import shutil
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple, Union

from core import state
from core.config import config
from core.logging_setup import logger
from core.utils import (
    allowed_file,
    file_md5,
    to_utf8_bytes,
    decode_text,
    make_student_key,
)
from services import analysis_service


def _mod():
    return analysis_service._analysis_modules()


def _validate_upload(file_bytes: bytes, original_name: str) -> Tuple[bool, str, bytes]:
    """
    内容级校验 + CSV 编码自动归一化：
      - 空文件拒
      - xlsx/docx 必须 PK 头
      - CSV/TXT：尝试 decode_text，能拿到文本就通过；并且 **把 bytes 转成 UTF-8（无 BOM）** 返回
    返回: (ok, err_msg, normalized_bytes)
    """
    if not file_bytes or not file_bytes.strip():
        return False, f"{original_name}（空文件）", b""
    suffix = Path(original_name).suffix.lower()
    if suffix in (".xlsx", ".docx"):
        if not file_bytes.startswith(b"PK"):
            return False, f"{original_name}（文件损坏或不是有效的 {suffix[1:].upper()} 文件）", b""
        return True, "", file_bytes
    if suffix == ".csv":
        # 尝试解码：失败则判定为损坏（理论上 binary 垃圾文件也能 decode 成功但乱码，概率低，后续 analyze_* 解析会再抛错）
        try:
            normalized_bytes, _enc, _note = to_utf8_bytes(file_bytes)
        except Exception as e:
            return False, f"{original_name}（编码校验失败：{str(e)[:30]}）", b""
        # 基本检查：至少包含 1 行文本且含有至少一个逗号分隔符（否则空行 CSV 也被当合法浪费资源）
        if len(normalized_bytes) < 3:
            return False, f"{original_name}（CSV 内容为空）", b""
        return True, "", normalized_bytes
    if suffix == ".txt":
        try:
            normalized_bytes, _, _ = to_utf8_bytes(file_bytes)
        except Exception as e:
            return False, f"{original_name}（编码校验失败：{str(e)[:30]}）", b""
        return True, "", normalized_bytes
    # 兜底（未知扩展名但在 allow_ext 里）
    return True, "", file_bytes


def _peek_csv_label(file_bytes_utf8: bytes) -> bool:
    """辅助：CSV 是否看起来像教学数据（至少一行有 2 列以上），失败也不拒绝（让 analyzer 自己判断）。"""
    try:
        text = file_bytes_utf8.decode("utf-8", errors="replace")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return False
        return lines[0].count(",") >= 1 or lines[0].count("\t") >= 1
    except Exception:
        return True


def _safe_name_from_rel(rel_path: str, original_name: str) -> str:
    """
    基于前端传来的 webkitRelativePath（含文件夹层级）生成磁盘上安全且唯一的文件名。
    避免：
      校区软件1-3班/项目0-Java语言概述.xlsx
      校区软件4-5班/项目0-Java语言概述.xlsx
    这种同名不同文件夹的文件互相覆盖 uploads/ 下的磁盘副本。
    规则：路径分隔符 / \ 先全局替换成 __，然后其他非法字符再替换成 _。
    """
    base = (rel_path or "").strip() or original_name or "unnamed"
    # 先全局替换斜杠成路径分隔符（必须先做，不能放在逐字符判断里，因为第一个分支会误保留）
    base = base.replace("\\", "__").replace("/", "__")
    out_chars: List[str] = []
    for ch in base:
        # 允许字符：字母数字 / 下划线 / 连字符 / 点 / 空格（不再允许斜杠，因为上面已经替换）
        if ch.isalnum() or ch in "-_. ":
            out_chars.append(ch)
        else:
            out_chars.append("_")
    return "".join(out_chars)


def process_upload(
    files: List[Union[Tuple[str, bytes], Tuple[str, bytes, str]]],
    mode: str = "merge"
) -> Dict[str, Any]:
    """
    处理一批上传文件。
    files 元组兼容两种长度（向后兼容：
      (original_name, raw_bytes)                 —— 老调用方（_tmp_smoke 脚本 / analysis_service restore）
      (original_name, raw_bytes, relative_path)  —— 新前端 files.py api_upload 带文件夹层级
    mode: 'merge' = 追加分析，不清理任何已上传/报告文件
          'overwrite' = 先清空 uploads 目录 + reports 目录下所有学生/汇总/实验级报告，然后重跑这批
    返回：{ 'ok': bool, 'message': str, 'results': list, 'rejected': list, 'skipped': list }
    """
    # --- 1. 先把 files 全部规范化成 3 元组 (original_name, raw_bytes, relative_path)
    norm_files: List[Tuple[str, bytes, str]] = []
    for t in files:
        if len(t) >= 3:
            norm_files.append((str(t[0]), bytes(t[1]), str(t[2])))
        else:
            n, rb = str(t[0]), bytes(t[1])
            norm_files.append((n, rb, n))

    with state.ANALYSIS_LOCK:
        results: List[Dict[str, Any]] = []
        quiz_results: List[Dict[str, Any]] = []
        unit_results: List[Dict[str, Any]] = []
        attendance_results: List[Dict[str, Any]] = []
        knowledge_results: List[Dict[str, Any]] = []
        experiment_results: List[Dict[str, Any]] = []
        skipped_duplicates: List[str] = []
        rejected: List[str] = []
        mod = _mod()

        if state.file_hashes is None:
            state.file_hashes = {}
        if state.latest_results is None:
            state.latest_results = []
        if state.latest_experiment_results is None:
            state.latest_experiment_results = []
        if state.latest_quiz_results is None:
            state.latest_quiz_results = []
        if state.latest_unit_results is None:
            state.latest_unit_results = []
        if state.latest_attendance_results is None:
            state.latest_attendance_results = []
        if state.latest_knowledge_results is None:
            state.latest_knowledge_results = []

        if mode == "overwrite":
            # 清空磁盘上的 uploads / reports（报告只删自动生成的 .txt/.csv，手动复制进 reports 的文件保留规则更安全：全删所有 txt/csv，不含子目录）
            try:
                if config.upload_dir.exists():
                    for f in config.upload_dir.iterdir():
                        if f.is_file():
                            try: f.unlink()
                            except Exception: pass
                if config.report_dir.exists():
                    for f in config.report_dir.iterdir():
                        if f.is_file() and f.suffix.lower() in (".txt", ".csv"):
                            try: f.unlink()
                            except Exception: pass
                state.file_hashes.clear()
                state.latest_results = []
                state.latest_experiment_results = []
                state.latest_quiz_results = []
                state.latest_unit_results = []
                state.latest_attendance_results = []
                state.latest_knowledge_results = []
                state.latest_agg_data = None
                # 报告列表缓存也清
                try:
                    from api.v1.reports import _LIST_CACHE as _rep_cache
                    _rep_cache.clear()
                except Exception:
                    pass
                analysis_service._save_analysis_snapshot()
            except Exception as e:
                logger.warning("覆盖模式清理历史失败（仍继续本次上传）：%s", e)

        config.upload_dir.mkdir(parents=True, exist_ok=True)
        config.temp_dir.mkdir(parents=True, exist_ok=True)

        for original_name, raw_bytes, relative_path in norm_files:
            if not original_name or not allowed_file(original_name):
                continue
            ok, err_msg, normalized_bytes = _validate_upload(raw_bytes, original_name)
            if not ok:
                rejected.append(err_msg)
                continue
            # 内容级去重：**同一份字节内容不管来自哪个文件夹、叫啥名，都跳过**
            # （随堂测验 xlsx 即使文件名相同，不同班级内容字节级几乎肯定不同，
            #  所以这个规则只挡"真·完全相同文件反复拖进来"，不会误挡 1-3 班 vs 4-5 班）
            digest = hashlib.md5(raw_bytes).hexdigest()
            safe_name = _safe_name_from_rel(relative_path, original_name)
            if digest in state.file_hashes:
                skipped_duplicates.append(
                    f"{safe_name}（内容已分析，重复跳过；已存在：{state.file_hashes[digest]}）"
                )
                continue
            store_path = config.upload_dir / safe_name
            store_bytes: bytes = normalized_bytes if normalized_bytes else raw_bytes
            store_path.write_bytes(store_bytes)
            state.file_hashes[digest] = safe_name
            temp_path = config.temp_dir / safe_name
            temp_path.write_bytes(store_bytes)

            entry: Dict[str, Any] = {
                "original_name": original_name,
                "safe_name": safe_name,
                "relative_path": relative_path,
                "source_type": "",
                "experiment_name": original_name,
                "report_filename": "",
                "student_count": 0,
                "weak_count": 0,
                "has_error": False,
            }
            suffix = Path(original_name).suffix.lower()
            try:
                if suffix == ".xlsx":
                    fn = original_name.lower()
                    rel_low = relative_path.lower()
                    if ("单元练习" in fn) or ("单元练习" in rel_low):
                        res = mod["analyze_unit_file"](str(temp_path))
                        res["uploaded_name"] = original_name
                        res["safe_name"] = safe_name
                        res["relative_path"] = relative_path
                        unit_results.append(res)
                        entry["source_type"] = "单元练习"
                        _dir_hint = relative_path.split("/")[-2] if "/" in relative_path.replace("\\","/") else ""
                        class_hint = res.get("class_name", "") or _dir_hint
                        entry["experiment_name"] = f"{class_hint + ' · ' if class_hint else ''}{original_name}"
                        entry["student_count"] = res.get("student_count", 0)
                    elif ("课堂活动" in fn) or ("分数明细" in fn) or ("课堂活动" in rel_low) or ("分数明细" in rel_low):
                        res = mod["analyze_attendance_file"](str(temp_path))
                        res["uploaded_name"] = original_name
                        res["safe_name"] = safe_name
                        res["relative_path"] = relative_path
                        attendance_results.append(res)
                        entry["source_type"] = "课堂活动"
                        _dir_hint = relative_path.split("/")[-2] if "/" in relative_path.replace("\\","/") else ""
                        class_hint = res.get("class_name", "") or _dir_hint
                        entry["experiment_name"] = f"{class_hint + ' · ' if class_hint else ''}{original_name}"
                        entry["student_count"] = res.get("student_count", 0)
                    else:
                        try:
                            quiz = mod["analyze_quiz_file"](str(temp_path))
                        except Exception:
                            quiz = None
                        if quiz:
                            quiz["uploaded_name"] = original_name
                            quiz["safe_name"] = safe_name
                            quiz["relative_path"] = relative_path
                            quiz_results.append(quiz)
                            entry["source_type"] = "随堂测验"
                            _dir_hint = relative_path.split("/")[-2] if "/" in relative_path.replace("\\","/") else ""
                            class_hint = quiz.get("class_name", "") or _dir_hint
                            proj = f"{quiz.get('project_id','')}-{quiz.get('chapter_name', Path(original_name).stem)}".strip("-")
                            entry["experiment_name"] = f"{class_hint + ' · ' if class_hint else ''}{proj}"
                            entry["student_count"] = quiz.get("student_count", 0)
                            entry["weak_count"] = quiz.get("weak_count", 0)
                        else:
                            fallback_ok = False
                            for key, fn_name in [
                                ("单元练习", "analyze_unit_file"),
                                ("课堂活动", "analyze_attendance_file"),
                                ("知识点掌握度", "analyze_knowledge_file"),
                            ]:
                                try:
                                    res = mod[fn_name](str(temp_path))
                                    res["uploaded_name"] = original_name
                                    res["safe_name"] = safe_name
                                    res["relative_path"] = relative_path
                                    if key == "单元练习": unit_results.append(res)
                                    elif key == "课堂活动": attendance_results.append(res)
                                    else: knowledge_results.append(res)
                                    class_hint = relative_path.split("/")[-2] if "/" in relative_path.replace("\\","/") else ""
                                    entry["source_type"] = key
                                    entry["experiment_name"] = f"{class_hint + ' · ' if class_hint else ''}{original_name}"
                                    entry["student_count"] = res.get("student_count", 0)
                                    fallback_ok = True
                                    break
                                except Exception:
                                    continue
                            if not fallback_ok:
                                # XLSX 所有匹配失败：不算 rejected（用户拖了不认识的 XLSX 不算 rejected 报错吓用户，只算 has_error=True 保留在 results 里但不进任何列表
                                entry["has_error"] = True
                                entry["error_msg"] = "XLSX 未匹配到任何分析器（非随堂测验/单元练习/课堂活动）"
                    results.append(entry)
                    temp_path.unlink(missing_ok=True)
                    continue
                # CSV：头歌/MOOC
                source_type = mod["detect_data_source"](str(temp_path))
                entry["source_type"] = source_type
                report_stem = "".join(
                    c if c.isalnum() or c in " -_" else "_" for c in Path(safe_name).stem
                )
                if source_type in ("touge", "auto"):
                    try:
                        result = mod["analyze_touge_file"](str(temp_path), state.knowledge_base)
                        report = mod["generate_touge_report"](result)
                        class_hint = relative_path.split("/")[-2] if "/" in relative_path.replace("\\","/") else ""
                        raw_exp = result.get("experiment_name", original_name)
                        entry["experiment_name"] = f"{class_hint + ' · ' if class_hint else ''}{raw_exp}"
                        entry["source_type"] = "touge"
                        sa = mod["analyze_all_students"](result, state.knowledge_base)
                        class_summary = mod["generate_class_summary"](sa)
                        (config.report_dir / f"汇总_{report_stem}.txt").write_text(
                            class_summary, encoding="utf-8"
                        )
                        for sr in sa.get("student_results", []):
                            name, sid = sr.get("name", ""), sr.get("student_id", "")
                            safe_key = make_student_key(name, sid)
                            # 个人报告命名优化：个人_姓名_学号_实验名.txt
                            # 实验名做截断，避免文件名过长
                            exp_name_clean = "".join(c if c.isalnum() or c in " -_" else "_" for c in result.get("experiment_name", original_name))
                            exp_name_short = exp_name_clean[:30]
                            srep = mod["generate_student_report"](
                                sr, result.get("experiment_name", "")
                            )
                            (config.report_dir / f"个人_{safe_key}_{exp_name_short}.txt").write_text(
                                srep, encoding="utf-8"
                            )
                        entry["student_count"] = result.get("student_count", 0)
                        entry["weak_count"] = sa.get("weak_student_count", 0)
                        entry["report_stem"] = report_stem
                        try:
                            (config.report_dir / f"{report_stem}.csv").write_bytes(store_path.read_bytes())
                        except Exception:
                            pass
                    except Exception:
                        result = mod["analyze_mooc_file"](str(temp_path), state.knowledge_base)
                        report = mod["generate_mooc_report"](result)
                        class_hint = relative_path.split("/")[-2] if "/" in relative_path.replace("\\","/") else ""
                        raw_class = result.get("classroom_name", original_name)
                        entry["experiment_name"] = f"{class_hint + ' · ' if class_hint else ''}{raw_class}"
                        entry["source_type"] = "mooc"
                else:
                    result = mod["analyze_mooc_file"](str(temp_path), state.knowledge_base)
                    report = mod["generate_mooc_report"](result)
                    class_hint = relative_path.split("/")[-2] if "/" in relative_path.replace("\\","/") else ""
                    raw_class = result.get("classroom_name", original_name)
                    entry["experiment_name"] = f"{class_hint + ' · ' if class_hint else ''}{raw_class}"
                    entry["source_type"] = "mooc"
                report_fn = f"报告_{report_stem}.txt"
                (config.report_dir / report_fn).write_text(report, encoding="utf-8")
                entry["report_filename"] = report_fn
            except Exception as e:
                entry["has_error"] = True
                entry["error_msg"] = str(e)
                logger.warning("解析文件失败 [%s / %s]: %s", safe_name, original_name, e, exc_info=True)
            finally:
                temp_path.unlink(missing_ok=True)
            results.append(entry)

        # 填充 experiment_results（从 report_stem 反查 CSV 副本）
        for r in [x for x in results if x["source_type"] == "touge" and not x.get("has_error")]:
            stem = None
            if r.get("report_filename"):
                stem = ".".join(r["report_filename"].split(".")[:-1])
            filepath = None
            for candidate in [
                config.report_dir / f"{stem}.csv" if stem else None,
                config.upload_dir / Path(r["safe_name"]),  # 优先 safe_name（带文件夹层级编码）
                config.upload_dir / Path(r["original_name"]).name,
                config.report_dir / f"{r.get('report_stem')}.csv" if r.get("report_stem") else None,
            ]:
                if candidate and candidate.exists():
                    filepath = candidate
                    break
            if filepath and filepath.exists():
                try:
                    exp = mod["analyze_touge_file"](str(filepath), state.knowledge_base)
                    # 把上传元信息挂进 exp，否则 _merge_combo 看不到 safe_name/class_name
                    # 两个不同班同名实验（如"Java入门-项目0-猜数字"）会被 only-p 当成重复误吞
                    exp["uploaded_name"] = r["original_name"]
                    exp["safe_name"] = r["safe_name"]
                    exp["relative_path"] = r.get("relative_path", "")
                    rel = r.get("relative_path", "").replace("\\", "/")
                    # class_name：取 relative_path 倒数第二级文件夹（苏理工-Java程序设计（2026）这种）
                    parts = [p for p in rel.split("/") if p]
                    if len(parts) >= 2:
                        exp["class_name"] = parts[-2]
                    else:
                        exp["class_name"] = r.get("experiment_name", "").split(" · ")[0] if " · " in r.get("experiment_name", "") else ""
                    experiment_results.append(exp)
                except Exception:
                    pass

        # 覆盖/合并
        if mode == "overwrite":
            state.latest_experiment_results = experiment_results
            state.latest_quiz_results = quiz_results
            state.latest_unit_results = unit_results
            state.latest_attendance_results = attendance_results
            state.latest_knowledge_results = knowledge_results
        else:
            def _merge_combo(dst: List[Dict[str, Any]], src: List[Dict[str, Any]], primary_key: str, secondary_key: str = "safe_name"):
                """
                组合键判重：
                  - safe: safe_name（带文件夹层级编码，最权威，有就直接判
                  - cls+p: class_name + primary_key（班级+主键
                  - cls+exp: class_name + experiment_name
                  - only-p: 如果 old/new 都没 safe_name 才退化用 primary_key（兼容纯文件名老数据）
                避免 1-3班/项目0.xlsx 和 4-5班/项目0.xlsx 上传文件名相同被误判丢弃。
                """
                def _fp(item: Dict[str, Any]) -> Set[str]:
                    fps: Set[str] = set()
                    p = item.get(primary_key)
                    s = item.get(secondary_key, "")
                    cls = item.get("class_name", "")
                    expn = item.get("experiment_name", "")
                    if s: fps.add(f"safe::{s}")
                    if cls and p: fps.add(f"cls+p::{cls}||{p}")
                    if cls and expn: fps.add(f"cls+exp::{cls}||{expn}")
                    if (not s) and p: fps.add(f"only-p::{p}")
                    return fps

                dst_fps = [_fp(x) for x in dst]
                for i in src:
                    ifp = _fp(i)
                    found = False
                    for d in dst_fps:
                        if ifp & d:
                            found = True; break
                    if not found:
                        dst.append(i)
                        dst_fps.append(ifp)

            _merge_combo(state.latest_experiment_results, experiment_results, "experiment_name", "safe_name")
            _merge_combo(state.latest_quiz_results, quiz_results, "uploaded_name", "safe_name")
            _merge_combo(state.latest_unit_results, unit_results, "uploaded_name", "safe_name")
            _merge_combo(state.latest_attendance_results, attendance_results, "uploaded_name", "safe_name")
            _merge_combo(state.latest_knowledge_results, knowledge_results, "uploaded_name", "safe_name")

        state.latest_results = results
        agg = analysis_service.rebuild_agg_data()

        # 新报告生成了 → 报告列表缓存清掉（下次打开报告页立刻看到新内容）
        try:
            from api.v1.reports import _LIST_CACHE as _rep_cache
            _rep_cache.clear()
        except Exception:
            pass

        # 构建返回消息
        if agg:
            action = "合并" if mode != "overwrite" else "覆盖（已清理历史）"
            parts: List[str] = []
            if agg["experiment_count"]:
                parts.append(f"{agg['experiment_count']} 个实验")
            if agg.get("quiz_count", 0):
                parts.append(f"{agg['quiz_count']} 次随堂测验")
            if len(state.latest_unit_results):
                parts.append(f"{len(state.latest_unit_results)} 份单元练习")
            if len(state.latest_attendance_results):
                parts.append(f"{len(state.latest_attendance_results)} 份课堂活动分数明细")
            message = f"已{action}分析" + "、".join(parts)
            eff_total = (state.latest_agg_data or {}).get("total_students", 0)
            if eff_total:
                message += f"，共 {eff_total} 名学生"
            if skipped_duplicates:
                message += f"；{len(skipped_duplicates)} 个文件已存在，未重复导入"
            if rejected:
                message += "；已拒绝损坏文件：" + "；".join(rejected)
        elif results and skipped_duplicates:
            message = f"已解析 {len(results)} 个文件；{len(skipped_duplicates)} 个文件已存在，未重复导入"
        else:
            if rejected:
                message = "以下文件未通过校验，已拒绝：" + "；".join(rejected)
            elif skipped_duplicates:
                message = f"所选文件均已上传过（{len(skipped_duplicates)} 个重复文件已跳过），无需重复分析"
            else:
                message = "没有可分析的文件"

        return {
            "ok": len(results) > 0,
            "message": message,
            "results": results,
            "rejected": rejected,
            "skipped": skipped_duplicates,
        }
