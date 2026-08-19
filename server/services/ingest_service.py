# _*_ coding : UTF-8 _*_
"""
离线数据入库（Ingest）服务：5 阶段流水线

  原始 CSV/XLSX (脏数据)
        ↓
  Phase 1 预清洗 → 读入 DataFrame，去纯空行，处理编码；拆成 (表头, rows)
        ↓
  Phase 2 智能列映射 → 识别列头，归一化为标准列名（student_id / name / class_name / final_score ...）
        ↓
  Phase 3 行分类 → 每一行打 row_type 标签（student / teacher_noise / header_noise / data_noise）
        ↓
  Phase 4 构建行文本 → 把一行学生数据翻译成自然语言描述，供 Embedding 模型编码向量
        ↓
  Phase 5 Embedding → 批量编码 → 幂等 upsert pgvector（唯一键 file_id+line_no）

设计原则：
- 解析规则不再写死在 touge_parser/mooc_parser；列映射只依赖列名别名表 + 学号正则
- 保证学生姓名 & 成绩不张冠李戴：列映射后 row_text 直接带 name/student_id 再编码
- 老师名（如 卢冶）直接按配置黑名单过滤不入库
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger

from core import state
from core.config import config
from core.db import PgvectorStore, run_migration_if_needed, pg_store
from core.utils import file_md5, safe_upload_path
from services.embedding import embedding

# ============================================================
# Phase 2: 列名别名表（多平台数据统一映射 → 标准列名）
# 覆盖头歌/MOOC/随堂测验/单元练习/课堂活动
# ============================================================
_COLUMN_ALIASES: Dict[str, List[str]] = {
    "student_id": [
        "student_id", "学号", "用户学号", "学生学号", "学生编号",
        "user_id", "用户编号", "账号", "学工号", "准考证号", "id",
    ],
    "name": [
        "user_name", "姓名", "学生姓名", "用户名", "name", "真实姓名",
        "学生", "学生名",
    ],
    "class_name": [
        "group_name", "班级", "班级名称", "class", "class_name",
        "所在班级", "行政班", "教学班", "group", "班级分组",
    ],
    "final_score": [
        "final_score", "总分", "得分", "成绩", "最终得分", "final",
        "分数", "sum_score", "总成绩", "总得分", "score", "百分制总分",
        "平均得分", "平均分",
    ],
    "weak_count": [
        "薄弱任务数", "未通过任务数", "不及格题数", "错题数",
        "error_count", "错误题数", "失败次数", "未完成次数",
    ],
    "task_count": [
        "任务总数", "总任务数", "task_count", "题目数", "总题数",
        "小题数", "任务数",
    ],
    "experiment_name": [
        "实验名称", "实验名", "experiment_name", "章节名称",
        "项目名称", "单元名称", "实验项目", "实验标题",
    ],
    "cost_time": [
        "cost_time", "用时", "耗时", "总耗时", "时间(秒)", "时间",
        "作答用时", "time_consuming",
    ],
}


def _build_col_lookup() -> Dict[str, str]:
    """把 _COLUMN_ALIASES 翻转成 {别名小写去空格: 标准列名}"""
    out: Dict[str, str] = {}
    for std, aliases in _COLUMN_ALIASES.items():
        out[std.lower().replace(" ", "").replace("_", "")] = std
        for a in aliases:
            key = a.lower().replace(" ", "").replace("_", "")
            out.setdefault(key, std)
    return out


_COL_LOOKUP = _build_col_lookup()


def _standardize_columns(df_columns: Iterable[str]) -> Dict[str, str]:
    """
    给一个 DataFrame 的原始列名列表，返回 {原始列名: 标准列名}
    没有匹配上的列不丢，也放进映射，标准名 = 原列名（保持原样不改名），
    这样所有 extra_cols 完整保留。
    """
    mapping: Dict[str, str] = {}
    for orig in df_columns:
        if not isinstance(orig, str):
            orig = str(orig)
        key = orig.lower().replace(" ", "").replace("_", "")
        mapping[orig] = _COL_LOOKUP.get(key, orig)
    return mapping


# ============================================================
# Phase 1 + 2: 读 CSV/XLSX → 规范化列名
# ============================================================
def _read_tabular(path: Path) -> pd.DataFrame:
    """统一读 CSV/XLSX → DataFrame。CSV 自动 try 多种编码。"""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
            try:
                return pd.read_csv(str(path), encoding=enc, dtype=str, keep_default_na=False)
            except UnicodeDecodeError:
                continue
            except Exception:
                continue
        # 最后兜一个 low_memory=False
        return pd.read_csv(str(path), dtype=str, keep_default_na=False, encoding_errors="ignore")
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(str(path), dtype=str, keep_default_na=False)
    raise ValueError(f"不支持的文件类型 (仅 CSV/XLSX): {suffix}")


# ============================================================
# 列名推断辅助：猜测 source_type
# ============================================================
def _detect_source_type(columns: List[str]) -> str:
    """根据列名粗判数据来源：touge/mooc/quiz/unit/attendance，用于后续元数据"""
    cols_l = {c.lower().replace(" ", "") for c in columns}
    # 头歌：user_name, student_id, group_name, 各种 task1_name
    if "task1_name" in cols_l or "user_name" in cols_l and "group_name" in cols_l:
        return "touge"
    if any("正确率" in str(c) for c in columns):
        return "quiz"
    if any("考勤" in str(c) or "出勤" in str(c) or "缺勤" in str(c) for c in columns):
        return "attendance"
    if any("章节" in str(c) or "单元测验" in str(c) for c in columns):
        return "unit"
    # MOOC：慕课导出通常带"用户名+学号+班级"
    if "用户名" in [str(c) for c in columns] and "学号" in [str(c) for c in columns]:
        return "mooc"
    return "touge"  # 兜底当 touge


# ============================================================
# Phase 3: 行级分类（过滤噪声行 / 老师行）
# ============================================================
_TEACHER_NAMES_SET = set(config.cleaning_teacher_names or [])
_NOISE_KEYWORDS_SET = set(config.cleaning_noise_keywords or [])
_STUDENT_ID_PATTERNS = [re.compile(p) for p in (config.cleaning_student_id_regex or [])]
_NAME_LEN_MIN, _NAME_LEN_MAX = config.cleaning_name_len_range


def _looks_like_student_id(val: str) -> bool:
    if not val or not isinstance(val, str):
        return False
    v = val.strip()
    if not v:
        return False
    return any(p.fullmatch(v) for p in _STUDENT_ID_PATTERNS)


def _chinese_name_chars_only(val: str) -> bool:
    if not val:
        return False
    return bool(re.fullmatch(r"[\u4e00-\u9fffA-Za-z·\. ]+", val.strip()))


def _classify_row(row: Dict[str, Any], std_cols_set: set) -> str:
    """
    返回: 'student' / 'teacher_noise' / 'header_noise' / 'data_noise'
    判定规则优先级：teacher → 噪声行 → 学号强特征的学生行 → 否则 data_noise
    """
    name_val = str(row.get("name", "") or "").strip()
    sid_val = str(row.get("student_id", "") or "").strip()
    class_val = str(row.get("class_name", "") or "").strip()

    # ---- 老师黑名单（命中则直接过滤）
    if name_val and name_val in _TEACHER_NAMES_SET:
        return "teacher_noise"

    # ---- 表头/班级标题行（整行出现"学号/姓名/班级/总分/序号/排名"等关键词
    joined_all = " ".join(str(v) for v in row.values() if v is not None).lower()
    noise_hit = sum(1 for kw in _NOISE_KEYWORDS_SET if kw.lower() in joined_all)
    if noise_hit >= 2 and not _looks_like_student_id(sid_val):
        # 两个以上噪声关键词 + 学号不匹配 → 基本就是表头/小计/说明行
        return "header_noise"

    # ---- 空姓名/学号 → 噪声
    if not name_val and not sid_val:
        return "data_noise"

    # ---- 学生行强特征：学号格式命中 OR (姓名是中文长度2-4 且 有班级 or 有分数)
    sid_ok = _looks_like_student_id(sid_val)
    name_ok = (
        bool(name_val)
        and _NAME_LEN_MIN <= len(name_val) <= _NAME_LEN_MAX
        and _chinese_name_chars_only(name_val)
        and not any(kw in name_val for kw in _NOISE_KEYWORDS_SET)
    )
    has_any_score = any(
        str(row.get(c, "")).strip().lstrip("-").replace(".", "", 1).isdigit()
        for c in ("final_score", "总分", "得分", "score", "成绩")
        if c in row
    )
    has_class = bool(class_val)
    if sid_ok and (name_ok or True):
        return "student"
    if name_ok and (sid_val or has_class or has_any_score):
        return "student"
    return "data_noise"


# ============================================================
# Phase 4: 构建一行学生的自然语言描述文本（供 embedding + LLM 引用）
# ============================================================
def _build_row_text(r_std: Dict[str, Any], source_type: str,
                    class_from_file: str, exp_from_file: str) -> str:
    name = r_std.get("name") or "未知姓名"
    sid = r_std.get("student_id") or "未知学号"
    cls = r_std.get("class_name") or class_from_file or "未识别班级"
    exp = r_std.get("experiment_name") or exp_from_file or "未识别实验"
    score = r_std.get("final_score")
    score_txt = f"总得分{score}分" if score not in (None, "") else "未记录总分"
    wc = r_std.get("weak_count")
    task_c = r_std.get("task_count")
    extra_parts: List[str] = []
    if wc:
        extra_parts.append(f"薄弱/错题数{wc}")
    if task_c:
        extra_parts.append(f"总任务数{task_c}")
    ct = r_std.get("cost_time")
    if ct:
        extra_parts.append(f"用时{ct}")
    extras_str = "，".join(extra_parts)
    source_txt = {
        "touge": "头歌实验平台", "mooc": "MOOC慕课平台",
        "quiz": "随堂测验", "unit": "MOOC单元练习",
        "attendance": "课堂考勤/活动",
    }.get(source_type, "教学数据平台")
    base = (
        f"【{source_txt}】学生姓名{name}（学号{sid}），"
        f"班级{cls}，所在实验/章节「{exp}」，{score_txt}"
    )
    if extras_str:
        base += f"，{extras_str}"
    # 其余非标准列（extra_cols），挑"看起来像分数"的拼一句，保证语义完整
    extras: List[str] = []
    for k, v in r_std.items():
        if k in {"name", "student_id", "class_name", "experiment_name",
                 "final_score", "weak_count", "task_count", "cost_time"}:
            continue
        if not k or v in (None, "", np.nan):
            continue
        vs = str(v).strip()
        if not vs:
            continue
        if vs.replace(".", "", 1).lstrip("-").isdigit() and len(vs) < 8:
            extras.append(f"{k} {vs}")
        elif len(vs) < 24 and len(extras) < 6:
            extras.append(f"{k}:{vs}")
    if extras:
        base += "；" + "，".join(extras)
    return base


# ============================================================
# Phase 0: 从 relative_path 推断 class_name / experiment_name
# ============================================================
def _guess_meta_from_paths(original_name: str, relative_path: str) -> Tuple[str, str]:
    """
    从文件夹层级和文件名推断 {班级名, 实验名}
    例如: "Java高级编程（2026）-1/xxx.csv" → class = "Java高级编程（2026）-1"
    """
    rp = (relative_path or "").replace("\\", "/")
    parts = [p for p in rp.split("/") if p]
    cls_from_dir = parts[-2] if len(parts) >= 2 else ""
    stem = Path(original_name).stem
    exp_from_stem = stem
    # 如果 stem 里有下划线前缀的数字 id（如头歌 3699835_xxx），剥掉
    if "_" in exp_from_stem:
        first, rest = exp_from_stem.split("_", 1)
        if first.isdigit() and 5 <= len(first) <= 8:
            exp_from_stem = rest
    return cls_from_dir.strip(), exp_from_stem.strip()


# ============================================================
# 对外主入口：处理单个上传数据文件 → 入库 pgvector
# ============================================================
def ingest_file(
    store_path: Path,
    *,
    file_hash: str,
    safe_name: str,
    original_name: str,
    relative_path: str,
    force: bool = False,
) -> Dict[str, Any]:
    """
    处理单个文件完整流水线（5 Phases）。返回 {ok, file_id, rows_total, rows_student...}。

    - 幂等：若 file_hash 已存在于 uploaded_files 且 rows_student > 0 且 !force → 直接返回，不重复入库。
    - 保证：同一文件无论上传多少次，student_rows 最多一份（ON CONFLICT UPDATE）。
    """
    # 0. 确保 DB migration 已执行（开发模式首次调用会自动建表）
    store_path = Path(store_path)
    ok, msg = run_migration_if_needed()
    if not ok and "不存在" not in msg and "已执行" not in msg:
        logger.warning("DB migration 未通过: %s", msg)

    file_size = store_path.stat().st_size if store_path.exists() else 0

    # 1. 先查 uploaded_files，若已 ingest 成功则跳过（文件级 MD5 去重）
    from core.db import db_session, UploadedFile
    with db_session() as s:
        existing = s.query(UploadedFile).filter_by(file_hash=file_hash).one_or_none()
        if (existing is not None
                and existing.status == "ingested"
                and existing.rows_student > 0
                and not force):
            return {
                "ok": True, "skipped": True, "file_id": existing.id,
                "file_hash": file_hash,
                "rows_total": existing.rows_total,
                "rows_student": existing.rows_student,
                "rows_noise": existing.rows_noise,
                "rows_teacher": existing.rows_teacher,
                "message": f"文件 {safe_name} 已入库（MD5 命中，跳过）",
            }

    # 拷贝到 temp 目录防文件锁（上传完 store_path 已经写好，但 pd.read_csv/excel 可能加锁）
    tmp = config.temp_dir / f"ingest_{file_hash[:12]}_{store_path.name}"
    shutil.copy2(str(store_path), str(tmp))
    try:
        df = _read_tabular(tmp)
    except Exception as e:
        logger.exception("读文件失败 [%s]: %s", store_path.name, e)
        return {"ok": False, "error": f"读取失败: {e}"}
    finally:
        try: tmp.unlink(missing_ok=True)
        except Exception: pass

    # Phase 2: 列映射
    col_map = _standardize_columns(df.columns.tolist())
    df_std = df.rename(columns=col_map)
    # 保证标准列存在（缺失的补空字符串，避免后面 KeyError）
    for std in ["student_id", "name", "class_name", "experiment_name",
                "final_score", "weak_count", "task_count", "cost_time"]:
        if std not in df_std.columns:
            df_std[std] = ""
    source_type = _detect_source_type(df_std.columns.tolist())

    cls_from_dir, exp_from_stem = _guess_meta_from_paths(original_name, relative_path)

    rows_total = len(df_std)
    rows_student = rows_noise = rows_teacher = 0

    # Phase 3+4: 遍历 → 分类 + 构造 row_text / payload
    #   先把 file 记录落盘（拿到 file_id）才能写 student_rows.file_id
    file_rec = PgvectorStore.upsert_uploaded_file(
        file_hash=file_hash, safe_name=safe_name, original_name=original_name,
        relative_path=relative_path, file_size=file_size,
        source_type=source_type,
        experiment_name=exp_from_stem or cls_from_dir or Path(original_name).stem,
    )
    file_id = file_rec.id

    payload_rows: List[Dict[str, Any]] = []
    row_texts: List[str] = []
    for idx, row in enumerate(df_std.itertuples(index=False), start=2):  # 行号从2开始(模拟第1行是表头)
        row_dict = {col: val for col, val in zip(df_std.columns.tolist(), list(row))}
        # 把"姓名/学号/班级/实验名"里如果有 NaN → 空字符串
        for k in ["name", "student_id", "class_name", "experiment_name"]:
            if k in row_dict and (row_dict[k] is None or (isinstance(row_dict[k], float) and np.isnan(row_dict[k]))):
                row_dict[k] = ""
        # 补齐 class_name/experiment_name 的全局上下文（文件夹名/文件名推断的当缺省）
        if not row_dict.get("class_name"):
            row_dict["class_name"] = cls_from_dir
        if not row_dict.get("experiment_name"):
            row_dict["experiment_name"] = exp_from_stem
        # 分类
        rtype = _classify_row(row_dict, set(df_std.columns.tolist()))
        if rtype == "teacher_noise":
            rows_teacher += 1
            continue
        if rtype in ("header_noise", "data_noise"):
            rows_noise += 1
            continue
        # student 行
        rows_student += 1
        try:
            final_score = float(row_dict.get("final_score")) if str(row_dict.get("final_score", "")).strip() not in ("", "-", "--") and str(row_dict.get("final_score", "")).replace(".", "", 1).lstrip("-").isdigit() else None
        except (TypeError, ValueError):
            final_score = None
        weak_count = 0
        wc_raw = str(row_dict.get("weak_count", "0") or "0")
        if wc_raw.isdigit():
            weak_count = int(wc_raw)
        task_count = 0
        tc_raw = str(row_dict.get("task_count", "0") or "0")
        if tc_raw.isdigit():
            task_count = int(tc_raw)
        # extra_cols: 所有非标准列 JSON 备份
        std_keys = {"name", "student_id", "class_name", "experiment_name",
                    "final_score", "weak_count", "task_count", "cost_time"}
        extra_cols = {
            k: (None if (isinstance(v, float) and np.isnan(v)) else v)
            for k, v in row_dict.items() if k not in std_keys
        }
        # 构建行文本 → 供 embedding + 后面 LLM 引用
        rtxt = _build_row_text(row_dict, source_type, cls_from_dir, exp_from_stem)
        # payload 等拿到 embedding 后再拼
        payload_rows.append({
            "line_no": idx,
            "row_type": rtype,
            "student_id": str(row_dict.get("student_id", "") or "").strip()[:64],
            "name": str(row_dict.get("name", "") or "").strip()[:64],
            "class_name": str(row_dict.get("class_name", "") or "").strip()[:256],
            "experiment_name": str(row_dict.get("experiment_name", "") or "").strip()[:512],
            "source_type": source_type,
            "final_score": final_score,
            "weak_count": weak_count,
            "task_count": task_count,
            "row_text": rtxt,
            "extra_cols": extra_cols,
        })
        row_texts.append(rtxt)

    # Phase 5: Embedding → upsert
    if row_texts:
        try:
            vectors = embedding.encode(row_texts)
            for p, vec in zip(payload_rows, vectors):
                p["embedding"] = vec
            inserted, updated = PgvectorStore.bulk_upsert_student_rows(
                file_id=file_id, rows=payload_rows,
                vector_dim=config.embedding_vector_dim,
            )
        except Exception as e:
            logger.exception("Embedding 或批量写入 DB 失败 [%s]: %s", store_path.name, e)
            PgvectorStore.mark_uploaded_rows(
                file_id, rows_total, rows_student, rows_noise, rows_teacher,
                error_msg=f"Embedding/写入失败: {e}",
            )
            return {"ok": False, "error": str(e)}
    else:
        inserted = updated = 0

    # 更新上传文件统计记录
    PgvectorStore.mark_uploaded_rows(
        file_id, rows_total, rows_student, rows_noise, rows_teacher,
    )
    return {
        "ok": True,
        "skipped": False,
        "file_id": file_id,
        "file_hash": file_hash,
        "rows_total": rows_total,
        "rows_student": rows_student,
        "rows_noise": rows_noise,
        "rows_teacher": rows_teacher,
        "inserted": inserted,
        "updated": updated,
        "source_type": source_type,
        "message": (
            f"入库完成：共{rows_total}行，学生行{rows_student}，"
            f"表头/噪声行{rows_noise}，老师行{rows_teacher}"
        ),
    }


# ============================================================
# 兼容旧接口：ingest_files 批处理（供 upload_service 直接调用）
# ============================================================
def ingest_files(files: Iterable[Dict[str, Any]], mode: str = "merge") -> Dict[str, Any]:
    """
    files: 每个元素必须包含 {store_path (Path), file_hash, safe_name, original_name, relative_path}
    mode: 'merge'=追加, 'overwrite' 先清掉 uploaded_files 全部再入库（慎用）
    返回: {ok, message, results: [...每个文件的ingest结果...]}
    """
    results: List[Dict] = []
    if mode == "overwrite":
        from core.db import db_session
        with db_session() as s:
            s.execute(text("TRUNCATE TABLE student_rows CASCADE"))
            s.execute(text("TRUNCATE TABLE uploaded_files CASCADE"))
            s.commit()
    for f in files:
        try:
            res = ingest_file(
                store_path=Path(f["store_path"]),
                file_hash=str(f["file_hash"]),
                safe_name=str(f["safe_name"]),
                original_name=str(f["original_name"]),
                relative_path=str(f.get("relative_path", f["original_name"])),
            )
            results.append(res)
        except Exception as e:
            logger.exception("单文件 ingest 异常: %s", e)
            results.append({"ok": False, "error": str(e), "original_name": f.get("original_name")})
    ok_count = sum(1 for r in results if r.get("ok"))
    skip_count = sum(1 for r in results if r.get("skipped"))
    student_total = sum(int(r.get("rows_student", 0) or 0) for r in results)
    message = (
        f"处理 {len(results)} 个文件：成功{ok_count}个，"
        f"跳过(重复MD5){skip_count}个，共入库学生行 {student_total} 条"
    )
    return {"ok": ok_count > 0, "message": message, "results": results}
