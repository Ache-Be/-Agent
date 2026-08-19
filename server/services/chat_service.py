# _*_ coding : UTF-8 _*_
"""
AI 对话服务：SSE 流式响应 + 上下文构建 + 问答沉淀
"""
import json
import re
import unicodedata
from typing import Any, Dict, Generator, List, Optional, Tuple

from core import state
from core.logging_setup import logger
from core.utils import (
    auto_title,
    list_conversations,
    get_conversation,
    save_conversation,
    delete_conversation,
    new_conv_id,
    load_api_key,
)
from datetime import datetime

from analysis.qa_sediment import save_qa, retrieve_qa, format_qa_ref, load_qa_logs, count_qa

# ============================================================
# 新架构：RAG 混合检索 Prompt 组装（pgvector + LLM）
# 若 pgvector 未就绪，回退到旧的 build_analysis_context + state.latest_agg_data 链路
# ============================================================
def _build_rag_system_prompt(user_msg: str) -> Tuple[str, Optional[List[float]]]:
    try:
        from services import rag_service
        sys_prompt, q_vec, _intent = rag_service.build_system_prompt(user_msg)
        return sys_prompt, q_vec
    except Exception as e:
        logger.warning("RAG prompt 构建失败，回退旧链路：%s", e)
        return "", None


# ============================================================
# 知识库检索（原 web.ai_assistant.retrieve_knowledge / format_knowledge_ref）
# ============================================================

def _normalize_for_match(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s or ""))
    return re.sub(r"[\s,，。.．、;；:：()（）【】\[\]'\"“”‘’\-—_/\\|]+", "", s).lower()


def retrieve_knowledge(query: str, knowledge_base: List[Dict], top_k: int = 5) -> List[Dict]:
    """语义检索 + 关键字兜底，返回命中的知识点条目列表。"""
    if not query or not knowledge_base:
        return []
    from analysis.knowledge_builder import search_knowledge
    hits = search_knowledge(query, knowledge_base)
    return hits[:top_k]


def format_knowledge_ref(records: List[Dict]) -> str:
    """把知识库命中结果格式化为 prompt 注入参考文本。"""
    if not records:
        return ""
    lines = [
        "\n## 相关知识点（MOOC 知识库，供回答参考）\n"
        "若以下知识点与数据分析结果相关，请结合数据引用；若不相关可忽略："
    ]
    for i, rec in enumerate(records, 1):
        name = str(rec.get("视频/知识点名称") or "").strip()
        unit = str(rec.get("MOOC教学单元") or "").strip()
        project = str(rec.get("项目名称") or "").strip()
        area = str(rec.get("知识领域") or "").strip()
        if not name:
            continue
        tag = "、".join(x for x in [area, unit, project] if x)
        lines.append(f"{i}. {name}" + (f"（{tag}）" if tag else ""))
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


# ============================================================
# 分析上下文构建（原 web.ai_assistant.build_analysis_context）
# ============================================================

def build_analysis_context(
    student_list: List[Any],
    top_error: List[Any],
    total_students: int,
    weak_count: int,
    experiment_count: int,
    quiz_count: int,
    unit_results: List[Any],
    attendance_results: List[Any],
    prediction_text: str = "",
    all_class_names: Optional[List[str]] = None,
    all_detected_years: Optional[List[str]] = None,
    all_experiment_names: Optional[List[str]] = None,
) -> str:
    """把当前聚合分析结果格式化为系统 prompt 中的"数据上下文"。"""
    parts = ["\n## 当前已掌握的教学分析数据（请严格基于以下数据回答）"]

    parts.append(f"\n- 覆盖学生总数：{total_students}")
    parts.append(f"- 薄弱学生数：{weak_count}")
    parts.append(f"- 头歌实验数：{experiment_count}")
    parts.append(f"- 随堂测验数：{quiz_count}")
    if unit_results:
        parts.append(f"- MOOC 单元测验数：{len(unit_results)}")
    if attendance_results:
        parts.append(f"- 课堂考勤数：{len(attendance_results)}")

    # 新增：整体名录（老师问"2026年软件2班怎么样"时，先让模型看到我们确实有这些班级/年份/实验，才能回答）
    if all_detected_years:
        parts.append("\n【已包含的学年/年份】" + "、".join(f"{y}年" for y in all_detected_years))
    if all_class_names:
        parts.append("\n【已包含的班级】（提问时可直接用这些班级名）：\n  - " + "\n  - ".join(all_class_names[:60]))
        if len(all_class_names) > 60:
            parts.append(f"  （共 {len(all_class_names)} 个班级，其余省略）")
    if all_experiment_names:
        parts.append("\n【已包含的头歌实验清单】（按实验名提问也可匹配）：")
        for i, name in enumerate(all_experiment_names[:25], 1):
            parts.append(f"  {i}. {name}")
        if len(all_experiment_names) > 25:
            parts.append(f"  （共 {len(all_experiment_names)} 个实验，其余省略）")

    if student_list:
        names: List[str] = []
        for s in student_list:
            if isinstance(s, dict):
                n = s.get("姓名") or s.get("name") or s.get("student_name") or ""
                sid = s.get("学号") or s.get("id") or s.get("student_id") or ""
                if n:
                    cls = s.get("class_names") or []
                    yrs = s.get("detected_years") or []
                    tag_parts = []
                    if isinstance(cls, list) and cls:
                        tag_parts.append("班:" + ",".join(cls[:2]))
                    if isinstance(yrs, list) and yrs:
                        tag_parts.append("年:" + ",".join(yrs[:2]))
                    tag = f"[{' | '.join(tag_parts)}]" if tag_parts else ""
                    names.append(f"{n}{'(' + str(sid) + ')' if sid else ''}{tag}")
            elif isinstance(s, str):
                names.append(s)
        if names:
            parts.append("\n【全部学生名单（可直接列出点名用）】\n" + "、".join(names))

    if top_error:
        parts.append("\n【班级高频错误 / 薄弱知识点 Top】")
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
                parts.append(line)
            else:
                parts.append(f"{i}. {item}")

    if unit_results:
        parts.append("\n【MOOC 单元测验整体情况】")
        for u in unit_results:
            if isinstance(u, dict):
                name = u.get("单元名称") or u.get("unit_name") or u.get("name") or str(u)
                avg = u.get("平均分") or u.get("avg_score") or u.get("平均分(百分制)") or ""
                weak = u.get("薄弱率") or u.get("weak_rate") or ""
                line = f"- {name}"
                if avg:
                    line += f"：平均分 {avg}"
                if weak:
                    line += f"，薄弱率 {weak}"
                parts.append(line)

    if attendance_results:
        parts.append("\n【课堂考勤情况】")
        for a in attendance_results:
            if isinstance(a, dict):
                name = a.get("课堂名称") or a.get("lecture") or a.get("name") or str(a)
                absent = a.get("缺勤人数") or a.get("absent") or ""
                late = a.get("迟到人数") or a.get("late") or ""
                line = f"- {name}"
                if absent:
                    line += f"：缺勤 {absent} 人"
                if late:
                    line += f"，迟到 {late} 人"
                parts.append(line)

    if prediction_text and str(prediction_text).strip():
        parts.append(f"\n【成绩预警 / 预测结论】\n{prediction_text.strip()}")

    return "\n".join(parts)


# ============================================================
# DeepSeek 流式 API（原 web.ai_assistant.chat_with_deepseek_stream）
# ============================================================

_DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
_DEFAULT_MODEL = "deepseek-chat"


def _parse_sse_chunks(raw: bytes) -> Tuple[List[str], bytes]:
    """从流式响应字节流中按 \\n\\n 切出完整 SSE 帧，返回 (frames, leftover)"""
    text = raw.decode("utf-8", errors="replace")
    # 兼容 LF / CRLF
    norm = text.replace("\r\n", "\n")
    frames = []
    idx = 0
    while True:
        sep = norm.find("\n\n", idx)
        if sep < 0:
            break
        frames.append(norm[idx:sep].strip())
        idx = sep + 2
    leftover = norm[idx:].encode("utf-8")
    return frames, leftover


def chat_with_deepseek_stream(
    api_key: str,
    messages: List[Dict[str, str]],
    model: str = _DEFAULT_MODEL,
    temperature: float = 0.6,
    max_tokens: int = 4096,
) -> Generator[str, None, None]:
    """调用 DeepSeek Chat Completions（stream=true），yield 增量 delta 文本。"""
    import requests

    if not api_key:
        raise ValueError("未配置 DeepSeek API Key")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    buf = b""
    with requests.post(_DEEPSEEK_URL, headers=headers, json=payload, stream=True, timeout=120) as resp:
        if resp.status_code != 200:
            txt = resp.text[:500]
            raise RuntimeError(f"DeepSeek API {resp.status_code}: {txt}")
        for chunk in resp.iter_content(chunk_size=2048):
            if not chunk:
                continue
            buf += chunk
            frames, buf = _parse_sse_chunks(buf)
            for frame in frames:
                if not frame:
                    continue
                # frame 可能由多行组成，找 "data:" 行
                data_line = None
                for ln in frame.split("\n"):
                    ln = ln.strip()
                    if ln.startswith("data:"):
                        data_line = ln[5:].strip()
                        break
                if not data_line or data_line == "[DONE]":
                    continue
                try:
                    obj = json.loads(data_line)
                except json.JSONDecodeError:
                    continue
                # DeepSeek / OpenAI 标准 stream 格式：choices[0].delta.content
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    yield content


def _recent_upload_context() -> str:
    lines = []
    for e in state.latest_results:
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
            for q in state.latest_quiz_results:
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
        "以下为最近一次上传的数据文件及分析摘要，请优先以这部分数据回答用户当前问题，\n"
        "历史上传数据与知识库内容仅作为补充参考。\n"
        + "\n".join(lines)
    )


def chat_stream(user_msg: str, conv_id: Optional[str] = None) -> Generator[str, None, None]:
    """SSE 流式返回 chat 事件（每一段 yield 'data: {...}\\n\\n' 格式）"""
    api_key = load_api_key()
    if not api_key:
        yield "event: error\ndata: " + json.dumps({"message": "请先配置 DeepSeek API Key"}, ensure_ascii=False) + "\n\n"
        return
    conv: Dict[str, Any]
    if conv_id:
        conv = get_conversation(conv_id) or {}
    if not conv_id or not conv:
        cid = new_conv_id()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        conv = {"id": cid, "title": "新对话", "created_at": now, "pinned": False, "messages": []}
    messages = conv.get("messages", [])

    # 初始化变量：避免后续 save_qa 引用时 NameError（任一链路都会赋值，但初始化更稳）
    hit_records: List[Dict] = []

    # =============== [NEW] 优先新链路 RAG system prompt ===============
    rag_system_prompt, query_vec = _build_rag_system_prompt(user_msg)
    system_content = ""
    if rag_system_prompt:
        system_content = rag_system_prompt
    else:
        # ---- 旧链路兜底（保证 pgvector 不可用也能回答）----
        system_content = (
            "你是一位教学预警系统的 AI 教学助手，职责是帮助教师分析学生实验数据。回答简洁、以数据为依据。"
            "如果当前没有可用的分析数据，或用户询问的数据不在你掌握的结果范围内，请如实告知"
            "（说明缺少哪部分数据），并提示用户先上传相应的 CSV/XLSX 数据文件，严禁编造学生成绩。"
            "学生姓名与学号属于教学分析数据：当用户询问学生名单、点名或具体学生时，"
            "可直接依据上下文中的『全部学生名单』如实列出，无需以隐私为由拒绝。"
            "当用户要求生成报告（如班级整体学习情况、学生个人报告、薄弱项分析等）时，"
            "请以清晰的 Markdown 结构输出：使用标题层级、表格、要点列表组织内容。"
        )
        ad = state.latest_agg_data
        if ad:
            context = build_analysis_context(
                student_list=ad.get("student_list", []),
                top_error=ad.get("top_error", []),
                total_students=ad.get("total_students", 0),
                weak_count=ad.get("weak_student_count", 0),
                experiment_count=ad.get("experiment_count", 0),
                quiz_count=ad.get("quiz_count", 0),
                unit_results=state.latest_unit_results,
                attendance_results=state.latest_attendance_results,
                prediction_text=ad.get("prediction_text", ""),
                all_class_names=ad.get("all_class_names", []),
                all_detected_years=ad.get("all_detected_years", []),
                all_experiment_names=ad.get("all_experiment_names", []),
            )
            system_content = f"{system_content}\n\n{context}"

        recent_ctx = _recent_upload_context()
        if recent_ctx:
            system_content = f"{system_content}\n\n{recent_ctx}"
        hit_records = retrieve_knowledge(user_msg, state.knowledge_base or [])
        kb_ref = format_knowledge_ref(hit_records)
        if kb_ref:
            system_content = f"{system_content}\n\n{kb_ref}"
        qa_ref = format_qa_ref(retrieve_qa(user_msg, top_k=2))
        if qa_ref:
            system_content = f"{system_content}\n\n{qa_ref}"

    # =============== 新链路：额外补 MOOC 知识库 & 旧 QA & 最近上传 上下文 ===============
    if rag_system_prompt:
        # MOOC 知识库（知识点参考）
        hit_records = retrieve_knowledge(user_msg, state.knowledge_base or [])
        kb_ref = format_knowledge_ref(hit_records)
        if kb_ref:
            system_content = f"{system_content}\n\n{kb_ref}"
        # 旧 qa_logs.jsonl 的沉淀（历史问答）
        qa_ref = format_qa_ref(retrieve_qa(user_msg, top_k=2))
        if qa_ref:
            system_content = f"{system_content}\n\n{qa_ref}"
        # 最近上传文件摘要
        recent_ctx = _recent_upload_context()
        if recent_ctx:
            system_content = f"{system_content}\n\n{recent_ctx}"

    built_messages: List[Dict[str, str]] = [{"role": "system", "content": system_content}]
    built_messages.extend(messages[-20:])
    built_messages.append({"role": "user", "content": user_msg})

    messages.append({"role": "user", "content": user_msg})

    yield f"data: {json.dumps({'event': 'init', 'conversation_id': conv['id']}, ensure_ascii=False)}\n\n"

    reply_parts: List[str] = []
    try:
        for chunk in chat_with_deepseek_stream(api_key, built_messages):
            reply_parts.append(chunk)
            yield f"data: {json.dumps({'event': 'delta', 'delta': chunk}, ensure_ascii=False)}\n\n"
    except Exception as e:
        err = f"请求出错：{str(e)}"
        reply_parts.append(err)
        yield f"data: {json.dumps({'event': 'delta', 'delta': err}, ensure_ascii=False)}\n\n"
    reply = "".join(reply_parts)

    try:
        save_qa(
            user_msg, reply,
            hit_knowledge=[r.get("视频/知识点名称", "") for r in hit_records],
            conversation_id=conv["id"],
        )
    except Exception as e:
        logger.warning("老版问答沉淀(qa_logs.jsonl)失败: %s", e)

    # ---- [NEW] 新版：pgvector 里也存一份（支持后续 RAG 历史语义检索）----
    try:
        if query_vec:
            from core.db import pg_store
            pg_store.save_qa(
                q=user_msg, a=reply,
                hit_knowledge=[r.get("视频/知识点名称", "") for r in hit_records],
                conv_id=conv["id"],
                embedding=query_vec,
            )
    except Exception as e:
        logger.warning("新版 pgvector QA 沉淀失败（不影响流式回答）: %s", e)

    messages.append({"role": "assistant", "content": reply})
    conv["messages"] = messages
    if conv.get("title", "新对话") == "新对话":
        conv["title"] = auto_title(messages)
    save_conversation(conv)

    yield f"data: {json.dumps({'event': 'done', 'conversation_id': conv['id'], 'title': conv['title']}, ensure_ascii=False)}\n\n"


def chat_non_stream(user_msg: str, conv_id: Optional[str] = None) -> Dict[str, Any]:
    """非流式聊天（兼容简单接口，目前保留作为备用。实际走 SSE 流式。）"""
    collected: List[str] = []
    last_done: Dict[str, Any] = {}
    for raw in chat_stream(user_msg, conv_id):
        # 一段 raw 可能包含多个 \n\n 分隔的 SSE 帧；每帧可能有 event: + data: 多行
        for frame in raw.split("\n\n"):
            if not frame.strip():
                continue
            event_name = "message"
            data_text = ""
            for ln in frame.split("\n"):
                ln = ln.rstrip()
                if ln.startswith("event:"):
                    event_name = ln[6:].strip()
                elif ln.startswith("data:"):
                    data_text = ln[5:].lstrip()
            if not data_text:
                continue
            try:
                obj = json.loads(data_text)
            except Exception:
                continue
            real = obj.get("event") or event_name
            if real == "delta":
                collected.append(obj.get("delta", ""))
            elif real == "error":
                collected.append(f"\n[错误] {obj.get('message') or '未知错误'}")
            elif real == "done":
                last_done = obj
    return {"reply": "".join(collected), **last_done}


def list_qa_logs(limit: int = 100) -> Dict[str, Any]:
    return {"total": count_qa(), "logs": load_qa_logs(limit=limit)}
