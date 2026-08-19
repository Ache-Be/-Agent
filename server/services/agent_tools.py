# _*_ coding : UTF-8 _*_
"""
Agent 工具层：function calling 的工具注册与执行。

设计：
- AGENT_TOOLS：OpenAI/DeepSeek 兼容的工具 JSON Schema，随每次请求发给模型；
- execute_tool()：执行模型请求的工具，返回可读文本回填给模型（role="tool"）。
  任何异常都被捕获并转成错误文本回填（模型可据此调整策略），绝不向上抛。

当前工具（全部为只读查询，无副作用）：
  1. query_student         查学生个体/列表的学习聚合数据（成绩、薄弱率、薄弱任务数）
  2. query_class_aggregate 查班级×实验聚合统计（平均分、薄弱率、最高/最低分等）
  3. search_knowledge_base 查 MOOC 知识库知识点
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger

from core import state
from core.db import pg_store

# ============================================================
# 合并班名展开（如"软件1-3班" → "软件1-3班（合并班：含软件1班、软件2班、软件3班）"）
# 数据中班级列常是合并班粒度，模型若只看到"软件1-3班"会误以为这是单个班，
# 也无法解释为什么查"软件1班"能命中它。
# ============================================================
_CLASS_EXPAND_RE = re.compile(r"^([\u4e00-\u9fffA-Za-z]+)(\d+)[-~～至到–—](\d+)(班|级|部|年级)?$")


def expand_class_label(cls: str) -> str:
    """把合并班名展开为带语义的标签；非合并班名原样返回。"""
    cls = (cls or "").strip()
    m = _CLASS_EXPAND_RE.match(cls)
    if not m:
        return cls
    prefix, lo, hi = m.group(1), int(m.group(2)), int(m.group(3))
    suffix = m.group(4) or "班"
    if hi <= lo:
        return cls
    # 区间过大（如"软件1-20班"）不逐个展开，只标注为合并班
    if hi - lo > 6:
        return f"{cls}（合并班，含{prefix}{lo}~{hi}班）"
    inner = "、".join(f"{prefix}{i}{suffix}" for i in range(lo, hi + 1))
    return f"{cls}（合并班：含{inner}）"

# ============================================================
# 工具 Schema（随请求发给模型）
# ============================================================
AGENT_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "query_student",
            "description": (
                "查询学生个体或学生列表的学习聚合数据。适合：问某个学生/某批学生学得怎么样、"
                "谁最薄弱、成绩排名、不及格/低于某分的学生名单等。可按学号/姓名/班级/分数上限过滤。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "student_id": {"type": "string", "description": "学生学号，如 252219605209"},
                    "name": {"type": "string", "description": "学生姓名，支持模糊匹配"},
                    "class_name": {"type": "string", "description": "班级名称，如 软件1班"},
                    "max_score": {"type": "number", "description": "平均分上限，查不及格/薄弱用（如低于60）"},
                    "weak_only": {"type": "boolean", "description": "为 true 时只看薄弱学生"},
                    "top_n": {"type": "integer", "description": "返回条数，默认 20，最大 100"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_class_aggregate",
            "description": (
                "查询班级×实验（或章节）的聚合统计。适合：问某个班整体学得怎么样、"
                "平均分/及格率/薄弱率/最高最低分、某实验各班的对比等。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "class_name": {"type": "string", "description": "班级名称，可留空表示全部班级"},
                    "experiment_name": {"type": "string", "description": "实验/项目/章节名称，可留空表示全部实验"},
                    "top_n": {"type": "integer", "description": "返回条数，默认 20，最大 100"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": (
                "在 MOOC 知识库中检索相关知识点（视频/知识点名称、教学单元、知识领域等）。"
                "适合：用户问到某个课程知识点、概念、MOOC 单元内容时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词/问题，如：循环结构、面向对象"},
                    "top_k": {"type": "integer", "description": "返回条数，默认 5，最大 10"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_student_trend",
            "description": (
                "查询学生/班级的跨实验分期成绩序列（按时间先后列出各实验平均分），"
                "用于判断成绩进步/退步/趋势。可按学号/姓名/班级过滤。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "student_id": {"type": "string", "description": "学生学号"},
                    "name": {"type": "string", "description": "学生姓名，支持模糊匹配"},
                    "class_name": {"type": "string", "description": "班级名称"},
                    "top_n": {"type": "integer", "description": "返回条数，默认 100"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_knowledge_gap",
            "description": (
                "查询班级高频错误/薄弱知识点（来自最近上传的数据分析结论），"
                "适合问哪些知识点没掌握、错题集中在哪。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "top_n": {"type": "integer", "description": "返回条数，默认 15"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_attendance",
            "description": (
                "查询最近上传的课堂考勤情况（各课堂缺勤/迟到人数）。"
                "注意：考勤数据只来自最近一次上传，没有上传过则返回空。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

# 工具结果回填给模型的内容长度上限（防止工具结果撑爆上下文）
_TOOL_RESULT_MAX_CHARS = 8000


# ============================================================
# 结果格式化（Markdown 文本，供模型直接引用）
# ============================================================
def _fmt_student_table(rows: List[Dict]) -> str:
    header = ["学号", "姓名", "班级", "实验数", "平均分", "薄弱率%", "薄弱任务数"]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for r in rows:
        lines.append(
            f"| {r.get('student_id','')} | {r.get('name','')} | {expand_class_label(r.get('class_name',''))} | "
            f"{r.get('experiment_count','')} | {r.get('avg_score','')} | "
            f"{r.get('weak_rate_percent','')} | {r.get('weak_count','')} |"
        )
    return "\n".join(lines)


def _fmt_class_table(rows: List[Dict]) -> str:
    header = ["班级", "实验/章节", "数据源", "学生数", "平均分", "薄弱率%", "最低分", "最高分", "中位数"]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for r in rows:
        lines.append(
            f"| {expand_class_label(r.get('class_name',''))} | {r.get('experiment_name','')} | "
            f"{r.get('source_type','')} | {r.get('student_count','')} | "
            f"{r.get('avg_score','')} | {r.get('weak_rate_percent','')} | "
            f"{r.get('min_score','')} | {r.get('max_score','')} | {r.get('median_score','')} |"
        )
    return "\n".join(lines)


# ============================================================
# 工具实现
# ============================================================
def _query_student(args: Dict[str, Any]) -> str:
    """查学生聚合数据（v_student_summary）。"""
    try:
        top_n = max(1, min(int(args.get("top_n") or 20), 100))
    except (TypeError, ValueError):
        top_n = 20
    try:
        rows = pg_store.student_summary(
            class_name=args.get("class_name"),
            student_id=args.get("student_id"),
            name=args.get("name"),
            max_avg_score=args.get("max_score"),
            min_weak_rate=30.0 if args.get("weak_only") else None,
            sort_by="weak_rate_percent",
            sort_desc=True,
            limit=top_n,
        )
    except Exception as e:
        logger.exception("query_student 查询失败: %s", e)
        return f"工具执行失败：{e}"
    if not rows:
        return "未查询到符合条件的学生数据。"
    return "### 学生数据（query_student）\n" + _fmt_student_table(rows)


def _query_class_aggregate(args: Dict[str, Any]) -> str:
    """查班级×实验聚合（v_class_summary）。"""
    try:
        top_n = max(1, min(int(args.get("top_n") or 20), 100))
    except (TypeError, ValueError):
        top_n = 20
    try:
        rows = pg_store.class_summary(
            class_name=args.get("class_name"),
            experiment_name=args.get("experiment_name"),
            limit=top_n,
        )
    except Exception as e:
        logger.exception("query_class_aggregate 查询失败: %s", e)
        return f"工具执行失败：{e}"
    if not rows:
        return "未查询到符合条件的班级聚合数据。"
    return "### 班级×实验聚合（query_class_aggregate）\n" + _fmt_class_table(rows)


def _search_knowledge_base(args: Dict[str, Any]) -> str:
    """检索 MOOC 知识库。"""
    query = str(args.get("query") or "").strip()
    if not query:
        return "检索关键词为空，请补充后再试。"
    try:
        top_k = max(1, min(int(args.get("top_k") or 5), 10))
    except (TypeError, ValueError):
        top_k = 5
    try:
        from analysis.knowledge_builder import search_knowledge
        hits = search_knowledge(query, state.knowledge_base or [])
    except Exception as e:
        logger.exception("search_knowledge_base 检索失败: %s", e)
        return f"工具执行失败：{e}"
    if not hits:
        return f"知识库中未检索到与「{query}」相关的知识点。"
    lines = [f"### 知识库命中（关键词：{query}）"]
    for i, rec in enumerate(hits[:top_k], 1):
        name = str(rec.get("视频/知识点名称") or rec.get("name") or "").strip()
        unit = str(rec.get("MOOC教学单元") or "").strip()
        area = str(rec.get("知识领域") or "").strip()
        project = str(rec.get("项目名称") or "").strip()
        tag = "、".join(x for x in [area, unit, project] if x)
        if not name:
            continue
        lines.append(f"{i}. {name}" + (f"（{tag}）" if tag else ""))
    return "\n".join(lines)


def _query_student_trend(args: Dict[str, Any]) -> str:
    """学生跨实验分期分数序列（趋势判断用）。"""
    try:
        top_n = max(1, min(int(args.get("top_n") or 100), 500))
    except (TypeError, ValueError):
        top_n = 100
    try:
        rows = pg_store.student_trend(
            student_id=args.get("student_id"),
            name=args.get("name"),
            class_name=args.get("class_name"),
            limit=top_n,
        )
    except Exception as e:
        logger.exception("query_student_trend 查询失败: %s", e)
        return f"工具执行失败：{e}"
    if not rows:
        return "未查询到该主体（学生/班级）的分期成绩数据。"
    lines = ["### 成绩分期序列（按时间先后排列，用于判断进步/退步）"]
    lines.append("| 顺序 | 班级 | 实验/章节 | 数据源 | 平均分 | 记录条数 | 最早时间 | 最晚时间 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows[:40], 1):
        lines.append(
            f"| {i} | {expand_class_label(r.get('class_name',''))} | {r.get('experiment_name','')} | "
            f"{r.get('source_type','')} | {r.get('avg_score','')} | {r.get('record_count','')} | "
            f"{str(r.get('first_seen'))[:10]} | {str(r.get('last_seen'))[:10]} |"
        )
    if len(rows) <= 1:
        lines.append("\n注意：该主体只有一期成绩数据，无法判断趋势变化，请如实告知老师数据不足。")
    return "\n".join(lines)


def _fmt_top_error(top_error: List[Any]) -> str:
    lines = ["### 班级高频错误 / 薄弱知识点（来自最近上传的分析）"]
    for i, item in enumerate(top_error[:15], 1):
        if isinstance(item, dict):
            name = item.get("知识点") or item.get("name") or item.get("知识点名称") or str(item)
            cnt = item.get("错误人数") or item.get("count") or item.get("人数") or ""
            rate = item.get("错误率") or item.get("rate") or ""
            line = f"{i}. {name}"
            if cnt:
                line += f"（{cnt}人出错"
                if rate:
                    line += f"，错误率 {rate}"
                line += "）"
            lines.append(line)
        else:
            lines.append(f"{i}. {item}")
    return "\n".join(lines)


def _query_knowledge_gap(args: Dict[str, Any]) -> str:
    """班级高频错误/薄弱知识点。"""
    try:
        top_n = max(1, min(int(args.get("top_n") or 15), 50))
    except (TypeError, ValueError):
        top_n = 15
    ad = state.latest_agg_data or {}
    top_error = ad.get("top_error") or []
    if not top_error:
        return "当前没有可用的薄弱知识点分析数据，请先上传相关数据文件。"
    return _fmt_top_error(top_error[:top_n])


def _query_attendance(args: Dict[str, Any]) -> str:
    """最近上传的考勤结果。"""
    results = state.latest_attendance_results or []
    if not results:
        return "当前没有可用的考勤数据，请先上传考勤 CSV/XLSX 文件。"
    lines = ["### 课堂考勤数据（最近上传）"]
    for a in results[:40]:
        if isinstance(a, dict):
            name = a.get("课堂名称") or a.get("lecture") or a.get("name") or str(a)
            absent = a.get("缺勤人数") or a.get("absent") or ""
            late = a.get("迟到人数") or a.get("late") or ""
            line = f"- {name}"
            if absent:
                line += f"：缺勤 {absent} 人"
            if late:
                line += f"，迟到 {late} 人"
            lines.append(line)
        else:
            lines.append(f"- {a}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


# 工具名 -> 实现函数
_TOOL_EXECUTORS: Dict[str, Any] = {
    "query_student": _query_student,
    "query_class_aggregate": _query_class_aggregate,
    "search_knowledge_base": _search_knowledge_base,
    "query_student_trend": _query_student_trend,
    "query_knowledge_gap": _query_knowledge_gap,
    "query_attendance": _query_attendance,
}


def safe_json_loads(raw: Any) -> Dict[str, Any]:
    """把模型的 tool_calls.arguments（JSON 字符串）解析为 dict，失败返回空 dict。"""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def execute_tool(name: str, args: Dict[str, Any]) -> str:
    """执行工具并返回回填文本；任何异常都被捕获转为错误文本，不向上抛。"""
    if not name:
        return "工具名为空，无法执行。"
    func = _TOOL_EXECUTORS.get(name)
    if func is None:
        return f"未知工具：{name}（可用工具：{', '.join(_TOOL_EXECUTORS)}）"
    if not isinstance(args, dict):
        args = {}
    try:
        result = func(args) or ""
    except Exception as e:  # 兜底：工具实现内部异常也转成文本，避免中断对话
        logger.exception("工具 %s 执行异常: %s", name, e)
        result = f"工具 {name} 执行异常：{e}"
    if len(result) > _TOOL_RESULT_MAX_CHARS:
        result = result[:_TOOL_RESULT_MAX_CHARS] + f"\n…（结果过长已截断，共 {len(result)} 字）"
    return result
