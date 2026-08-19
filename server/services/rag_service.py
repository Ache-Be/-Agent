# _*_ coding : UTF-8 _*_
"""
RAG 在线服务：用户提问 → 结构化条件 + 向量相似度混合检索 → 拼 prompt → LLM 流式回答

输入阶段：
  (A) 用户提问 → 先做「结构化意图识别」（轻量规则，不依赖 LLM）
      例如：
        - 命中学号正则 + 姓名模式 → 自动加 student_id / name WHERE
        - 命中"班/班级/软件2班"等 → class_name WHERE
        - 命中"实验x/项目x/第x章" → experiment_name WHERE
        - 命中"低于60/不到60/不及格/薄弱/挂科" → final_score < 60
        - 命中"平均分/最高分/最低分/薄弱率"等 → 直接走 SQL 聚合视图（不走向量）→ 纯 SQL 回答
  (B) Embedding → hybrid_search 混合检索 top_k=30~50 行
  (C) 聚合统计（SQL聚合视图）+ RAG 上下文 → 一起注入 system prompt

输出阶段：
  复用 chat_service 里的 chat_with_deepseek_stream()，保证流式响应格式一致。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Generator, List, Optional, Tuple

from loguru import logger

from core import state
from core.db import pg_store
from services.embedding import embedding

# ============================================================
# 结构化意图识别：轻量规则（把自然语言里的过滤条件抽出来）
# ============================================================
_NAME_BLACKLIST = {
    "有哪些", "怎么样", "是多少", "好不好", "怎么样", "情况如何", "有多少",
    "是什么", "为什么", "怎么办", "哪里", "哪些", "多少", "怎么", "什么", "这个",
}
_CLASS_RE = re.compile(
    r"(软件|计科|计算机|人工智能|大数据|物联网|信管|电商|工商管理)"
    r"(\d{1,4})(?:[\-_~\u2013\u2014至到]\d{0,4})?\s*(?:班|级|部|年级)?"
    r"|[\u4e00-\u9fffA-Za-z]{1,8}[\-_]?\d{1,4}(?:[\-_~\u2013\u2014至到]\d{0,4})?\s*(班|级)"
)
_EXPERIMENT_RE = re.compile(
    r"(实验|项目|章节|第[一二三四五六七八九十0-9]+[章节个])"
    r"[\u4e00-\u9fffA-Za-z0-9_\- ]{0,20}?(?=[，。,\s的是有与和跟对？?！!]|$)"
    r"|(Java[_\- \u4e00-\u9fffA-Za-z0-9]{0,30})"
)
_SCORE_UNDER_RE = re.compile(r"低于\s*(\d+)|不到\s*(\d+)|不及格|挂科|薄弱|小于\s*(\d+)")
_SCORE_OVER_RE = re.compile(r"高于\s*(\d+)|超过\s*(\d+)|大于\s*(\d+)|及格以上|(\d+)分以上")
_SID_RE = re.compile(r"\b2[0-9]\d{10}\b|\b20\d{6,10}\b|\b\d{8,14}\b")
_NAME_RE = re.compile(
    r"同学([\u4e00-\u9fff]{2,4})"
    r"|学生\s*([\u4e00-\u9fff]{2,4})"
    r"|姓名[:：]?\s*([\u4e00-\u9fff]{2,4})"
    r"|(?:^|[\s，。,])((?:(?!同学|学生|姓名|班级|学号|实验|项目|平均|多少|哪些|怎么|什么|这个|那个|请问|帮我|分析|查看|查询|列出|统计)[\u4e00-\u9fff]){2,4})(?=$|[\s，。,同学学生班级学号实验项目的是了在和跟对])"
)
_AGG_INTENT_RE = re.compile(
    r"(平均分|平均|最高分|最低分|排名|薄弱率|及格率|统计|整体情况|总体|多少人|"
    r"分布|前\d+名|后\d+名|排行榜|汇总|报表)"
)


def _extract_intent(user_msg: str) -> Dict[str, Any]:
    """
    从用户提问抽取结构化检索条件。
    返回:
       {student_id, name, class_name, experiment_name,
        min_score, max_score,
        is_agg_intent (是否需要聚合统计),
        vector_only (是否纯语义, 例如"薄弱的知识点")}
    """
    out: Dict[str, Any] = {
        "student_id": None, "name": None, "class_name": None,
        "experiment_name": None, "min_score": None, "max_score": None,
        "is_agg_intent": False, "vector_only": False,
    }
    # 学号
    m = _SID_RE.search(user_msg)
    if m:
        out["student_id"] = m.group(0)
    # 姓名
    for nm in _NAME_RE.finditer(user_msg):
        pick = next((g for g in nm.groups() if g), None)
        if pick and pick not in _NAME_BLACKLIST and not any(
            b in pick for b in ("平均", "多少", "哪些", "怎么", "什么", "这个", "那个",
                                 "班级", "学号", "实验", "项目", "学生", "同学")
        ):
            out["name"] = pick
            break
    # 班级
    cm = _CLASS_RE.search(user_msg)
    if cm:
        out["class_name"] = cm.group(0)
    # 实验名
    em = _EXPERIMENT_RE.search(user_msg)
    if em:
        out["experiment_name"] = em.group(0)
    # 分数范围: 低于60/不及格 → max=60
    mu = _SCORE_UNDER_RE.search(user_msg)
    if mu:
        pick = next((g for g in mu.groups() if g and g.isdigit()), None)
        out["max_score"] = float(pick) if pick else 60.0
    mo = _SCORE_OVER_RE.search(user_msg)
    if mo:
        pick = next((g for g in mo.groups() if g and g.isdigit()), None)
        out["min_score"] = float(pick) if pick else 60.0
    # 是否聚合类问题（有就先查 SQL 聚合视图结果放 prompt 里 LLM 直接引用数据）
    if _AGG_INTENT_RE.search(user_msg) or (
        (out["class_name"] or out["experiment_name"])
        and not out["student_id"] and not out["name"]
    ):
        out["is_agg_intent"] = True
    # 纯语义模式：没抽出来任何结构化条件
    if not any(out.get(k) for k in ("student_id", "name", "class_name",
                                     "experiment_name", "min_score", "max_score")):
        out["vector_only"] = True
    return out


# ============================================================
# 检索与 prompt 组装
# ============================================================
def _format_sql_agg_context(intent: Dict[str, Any]) -> str:
    """按抽取的条件查 v_student_summary / v_class_summary 聚合视图，拼自然语言文本"""
    parts: List[str] = []
    if not intent.get("is_agg_intent"):
        return ""
    # 班级维度聚合
    try:
        class_rows = pg_store.class_summary(
            class_name=intent.get("class_name"),
            experiment_name=intent.get("experiment_name"),
            limit=50,
        )
    except Exception as e:
        logger.warning("class_summary 查询失败: %s", e)
        class_rows = []
    if class_rows:
        header = [
            "班级", "实验/章节", "数据源", "学生数",
            "平均分", "薄弱率%", "最低分", "最高分", "中位数"
        ]
        lines = ["| " + " | ".join(header) + " |",
                 "|" + "|".join(["---"] * len(header)) + "|"]
        for r in class_rows[:30]:
            lines.append(
                f"| {r.get('class_name','')} | {r.get('experiment_name','')} | "
                f"{r.get('source_type','')} | {r.get('student_count','')} | "
                f"{r.get('avg_score','')} | {r.get('weak_rate_percent','')} | "
                f"{r.get('min_score','')} | {r.get('max_score','')} | "
                f"{r.get('median_score','')} |"
            )
        parts.append("### 班级 × 实验聚合统计（来自 SQL 聚合视图）\n" + "\n".join(lines))
    # 学生维度聚合（查薄弱学生 Top）
    try:
        stu_rows = pg_store.student_summary(
            class_name=intent.get("class_name"),
            student_id=intent.get("student_id"),
            name=intent.get("name"),
            min_weak_rate=30.0 if intent.get("max_score") or (not intent.get("name") and not intent.get("student_id")) else None,
            max_avg_score=intent.get("max_score"),
            sort_by="weak_rate_percent" if intent.get("is_agg_intent") else "avg_score",
            sort_desc=True,
            limit=50,
        )
    except Exception as e:
        logger.warning("student_summary 查询失败: %s", e)
        stu_rows = []
    if stu_rows:
        header = ["学号", "姓名", "班级", "实验数", "平均分", "薄弱率%", "薄弱任务数"]
        lines = ["| " + " | ".join(header) + " |",
                 "|" + "|".join(["---"] * len(header)) + "|"]
        for r in stu_rows[:40]:
            lines.append(
                f"| {r.get('student_id','')} | {r.get('name','')} | {r.get('class_name','')} | "
                f"{r.get('experiment_count','')} | {r.get('avg_score','')} | "
                f"{r.get('weak_rate_percent','')} | {r.get('weak_count','')} |"
            )
        parts.append("### 学生聚合 Top（来自 SQL 视图 v_student_summary）\n" + "\n".join(lines))
    return "\n\n".join(parts) if parts else ""


def _format_rag_rows_context(rows: List[Dict]) -> str:
    """把 hybrid_search 返回的行列表格式化为 prompt 参考文本"""
    if not rows:
        return ""
    lines = [
        "### RAG 语义匹配的学生行参考（相似度从高到低）",
        "提示：优先引用这些学生行的 row_text 和结构化列，无法匹配的如实回答缺少数据。",
    ]
    for i, r in enumerate(rows[:30], 1):
        sim = r.get("similarity") or 0
        score_txt = f", 最终得分{r['final_score']}" if r.get("final_score") is not None else ""
        lines.append(
            f"{i}. 【相似度{sim*100:.1f}%】{r.get('name','')}(学号{r.get('student_id','')} | "
            f"班级{r.get('class_name','')} | 实验{r.get('experiment_name','')}{score_txt})\n"
            f"   原文：{r.get('row_text','')}"
        )
    return "\n".join(lines)


def _format_qa_history_context(query_vec: List[float], top_k: int = 3) -> str:
    """历史问答沉淀检索（语义相似度），返回 prompt 文本"""
    try:
        rows = pg_store.retrieve_qa(query_vec, top_k=top_k)
    except Exception as e:
        logger.warning("retrieve_qa 失败: %s", e)
        return ""
    if not rows:
        return ""
    lines = ["### 历史相关问答（仅供参考，不要重复回答）"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. Q: {r.get('user_question','')} （相似度 {float(r.get('similarity',0))*100:.1f}%）")
        lines.append(f"   A: {r.get('assistant_reply','')[:500]}")
    return "\n".join(lines)


def build_retrieval_context(user_msg: str,
                            top_k_rows: int = 40) -> Tuple[str, List[float], Dict]:
    """
    在线检索主函数：
      1) 意图抽取
      2) query embedding
      3) SQL 聚合上下文 + 混合检索行上下文 + 历史 QA 上下文
    返回 (prompt_context_text, query_vector, intent)
    """
    intent = _extract_intent(user_msg)
    q_vec = embedding.encode_one(user_msg)

    # 1) 结构化混合检索行
    try:
        rag_rows = pg_store.hybrid_search(
            q_vec,
            student_id=intent.get("student_id"),
            name=intent.get("name"),
            class_name=intent.get("class_name"),
            experiment_name=intent.get("experiment_name"),
            min_score=intent.get("min_score"),
            max_score=intent.get("max_score"),
            top_k=top_k_rows,
            vector_only=intent.get("vector_only", False),
        )
    except Exception as e:
        logger.exception("hybrid_search 异常: %s", e)
        rag_rows = []

    # 2) SQL 聚合上下文
    agg_ctx = _format_sql_agg_context(intent)

    # 3) 行级 RAG 上下文
    rows_ctx = _format_rag_rows_context(rag_rows)

    # 4) 历史问答沉淀
    qa_ctx = _format_qa_history_context(q_vec, top_k=3)

    parts = [agg_ctx, rows_ctx, qa_ctx]
    ctx_text = "\n\n".join([p for p in parts if p])
    return ctx_text, q_vec, intent


# ============================================================
# LLM 系统 prompt 组装（加严格的不能编造数据限制）
# ============================================================
_SYSTEM_BASE = (
    "你是一位教学预警系统的 AI 教学助手，主要职责是基于系统已上传的学生数据，"
    "帮老师解读数据、生成个性化分析报告和建议。回答要求：\n"
    "1. 数据严格依据下方给出的【SQL 聚合统计】和【RAG 匹配行】。如果缺少相关班级/实验/学生数据，"
    "   必须明确回答「当前缺少 XXX 数据，请先上传 XXX 类型的 CSV/XLSX 文件」，严禁编造成绩或名单。\n"
    "2. 学号/姓名/班级/实验名属于教学数据，可以直接列出，不用以隐私为由拒绝回答点名/名单/薄弱学生Top等问题。\n"
    "3. 用户要求生成报告（班级整体/学生个人/薄弱知识点分析等）时，请用清晰的 Markdown 结构："
    "   标题层级 + 表格 + 要点列表。\n"
    "4. 用中文回答，简洁专业，结论以数据为依据，不要空洞套话。\n\n"
    "以下是本次系统检索到的上下文（请严格依据这些数据）：\n"
)


def build_system_prompt(user_msg: str) -> Tuple[str, List[float], Dict]:
    """返回 (system_prompt, query_embedding_vec, intent_dict)"""
    ctx, vec, intent = build_retrieval_context(user_msg)
    if not ctx:
        ctx = (
            "【当前暂无可检索的教学数据】——请先通过上传页面上传头歌/MOOC/随堂/考勤 CSV/XLSX 文件，"
            "或通过『数据文件』页面确认上传情况。"
        )
    prompt = f"{_SYSTEM_BASE}\n{ctx}\n"
    return prompt, vec, intent
