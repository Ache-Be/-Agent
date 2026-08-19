# _*_ coding : UTF-8 _*_
"""
AI 对话服务：SSE 流式响应 + 上下文构建 + 问答沉淀
"""
import json
import re
import unicodedata
from typing import Any, Dict, Generator, List, Optional, Tuple

from core import state
from core.config import config
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
from services.agent_tools import AGENT_TOOLS, execute_tool, safe_json_loads
from services.intent_router import classify
from services.chat_flows import FLOWS, generate_report_docx

# ============================================================
# 新架构：RAG 混合检索 Prompt 组装（pgvector + LLM）
# 若 pgvector 未就绪，回退到旧的 build_analysis_context + state.latest_agg_data 链路
# ============================================================
def _build_rag_system_prompt(user_msg: str) -> Tuple[str, Optional[List[float]]]:
    try:
        from services import rag_service
        from core.utils import load_chat_flags
        include_qa = bool(load_chat_flags().get("enable_qa_sediment_ref", True))
        sys_prompt, q_vec, _intent = rag_service.build_system_prompt(user_msg, include_qa_history=include_qa)
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

# ============ 对话记忆压缩（控制注入 prompt 的 token 成本） ============
# 思路：对话全部消息仍完整落盘（前端历史展示不受影响），但注入 prompt 时
# 只带「滚动摘要 + 最近 _RAW_HISTORY_WINDOW 条原始消息」，取代原来无脑塞最近 20 条。
_RAW_HISTORY_WINDOW = 6    # 每次请求注入的最近原始消息条数
_SUMMARY_INTERVAL = 6      # 每攒够这么多条新消息，把最早的一批滚入摘要
_SUMMARY_MAX_CHARS = 1200  # 滚动摘要最长保留长度（超出裁掉最旧部分，保证成本有界）
_TOOL_MAX_ROUNDS = int(config.get("chat.max_tool_rounds", 3))  # function calling 最大工具调用轮次
_SUMMARY_PROMPT = (
    "你在为一段较早的师生问答对话生成滚动摘要，作为后续对话的背景记忆。"
    "请用简洁中文，用 3~5 句话概括这段对话的核心内容，必须保留：学生姓名/学号、班级、"
    "实验名称、关键数据结论、老师已做出的判断或决定。不要复述整段对话，不要输出任何前缀语。"
)


def _parse_sse_chunks(raw: bytes) -> Tuple[List[str], bytes]:
    """从流式字节流中按 \\n\\n 切出完整 SSE 帧，返回 (frames, leftover)。

    字节级切分：先按 \\n\\n 分隔符（\\r\\n 已归一）定位完整帧，只解码完整的
    UTF-8 字节序列；chunk 边界恰好切在中文等多字节字符中间时，半个字符的
    原始字节原样留在 leftover 等下一批拼接，避免 decode replace 产生乱码。
    （\\n\\n 是 ASCII，不可能出现在多字节字符内部，故最后一个 \\n\\n 之前的
    字节序列必定 UTF-8 完整，可安全解码。）
    """
    if not raw:
        return [], b""
    norm = raw.replace(b"\r\n", b"\n")
    sep = norm.rfind(b"\n\n")
    if sep < 0:
        return [], norm
    complete, tail = norm[:sep], norm[sep + 2:]
    frames = [
        f.decode("utf-8", errors="replace").strip()
        for f in complete.split(b"\n\n")
        if f.strip()
    ]
    return frames, tail


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


def chat_with_tools_stream(
    api_key: str,
    messages: List[Dict[str, str]],
    tools: Optional[List[Dict]] = None,
    model: str = _DEFAULT_MODEL,
    temperature: float = 0.6,
    max_tokens: int = 4096,
) -> Generator[Any, None, None]:
    """流式调用 DeepSeek，支持 function calling。

    产出项（两种）：
      - str            文本增量 delta，调用方直接转发给前端；
      - dict {"_tool_calls": [...]}  本轮模型请求调用的工具列表，调用方执行后把
        assistant(tool_calls) 和 role="tool" 结果消息追加进 messages 再重新调用本生成器。

    工具调用的参数（arguments）在流里是分片 JSON 文本，按 index 累积拼接。
    """
    import requests

    if not api_key:
        raise ValueError("未配置 DeepSeek API Key")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
    # 流式工具调用按 index 累积：{"index": {"id","name","arguments"}}
    tool_slots: Dict[int, Dict[str, str]] = {}
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
                choices = obj.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if content:
                    yield content
                # 流式工具调用：arguments 按 index 分片，需要跨帧拼接
                for tc in delta.get("tool_calls") or []:
                    try:
                        idx = int(tc.get("index", 0))
                    except (TypeError, ValueError):
                        idx = 0
                    slot = tool_slots.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] = slot["arguments"] + fn["arguments"]
    # 流结束：如果累积到工具调用，产出工具调用标记（即便 finish_reason 异常缺失）
    calls = []
    for idx in sorted(tool_slots.keys()):
        slot = tool_slots[idx]
        if not slot.get("name"):
            continue
        calls.append({
            "id": slot["id"] or f"call_{idx}",
            "type": "function",
            "function": {"name": slot["name"], "arguments": slot["arguments"] or "{}"},
        })
    if calls:
        yield {"_tool_calls": calls}


def _post_chat_once(
    api_key: str,
    messages: List[Dict[str, str]],
    model: str = _DEFAULT_MODEL,
    temperature: float = 0.3,
    max_tokens: int = 512,
) -> Optional[str]:
    """非流式单次调用 DeepSeek（用于滚动摘要等轻量任务），失败返回 None。"""
    import requests

    if not api_key:
        return None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        resp = requests.post(_DEEPSEEK_URL, headers=headers, json=payload, timeout=60)
    except Exception as e:
        logger.warning("摘要调用网络异常: %s", e)
        return None
    if resp.status_code != 200:
        logger.warning("摘要调用失败 %s: %s", resp.status_code, resp.text[:200])
        return None
    try:
        data = resp.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
    except Exception as e:
        logger.warning("摘要响应解析失败: %s", e)
        return None
    return (content or "").strip() or None


def _summarize_messages(api_key: str, msgs: List[Dict]) -> Optional[str]:
    """把一段对话消息压缩成几句话；失败返回 None（调用方不回删原文，下次重试）。"""
    if not api_key or not msgs:
        return None
    try:
        text = "\n".join(
            f"{m.get('role', '?')}: {str(m.get('content', ''))[:2000]}" for m in msgs
        )
        built: List[Dict[str, str]] = [
            {"role": "system", "content": _SUMMARY_PROMPT},
            {"role": "user", "content": f"以下是要压缩的对话：\n{text}"},
        ]
        return _post_chat_once(api_key, built)
    except Exception as e:
        logger.warning("对话滚动摘要生成失败（保留原文，下次再试）: %s", e)
        return None


def _roll_summary(conv: Dict[str, Any], api_key: str) -> None:
    """对话记忆滚动压缩：
    消息攒够 _SUMMARY_INTERVAL 条后，把最早的一批压成摘要存进 conv["summary"]，
    并记录已覆盖到第几条（conv["summary_upto"]）。原文仍完整保留在 messages 里，
    前端历史展示不受影响；注入 prompt 时只带摘要 + 最近窗口的原始消息。
    任何失败都只记日志，不删消息、不抛异常。
    """
    try:
        msgs = conv.get("messages") or []
        upto = int(conv.get("summary_upto", 0) or 0)
        # 最后一条 assistant（当前轮）不回滚，避免把刚答完的话立即压掉
        end = min(upto + _SUMMARY_INTERVAL, len(msgs) - 1)
        if end <= upto or upto >= len(msgs):
            return
        chunk = msgs[upto:end]
        if not chunk:
            return
        digest = _summarize_messages(api_key, chunk)
        if not digest:
            return
        prev = str(conv.get("summary", "") or "").strip()
        merged = f"{prev}\n{digest}".strip() if prev else digest
        if len(merged) > _SUMMARY_MAX_CHARS:
            merged = merged[-_SUMMARY_MAX_CHARS:]
        conv["summary"] = merged
        conv["summary_upto"] = end
    except Exception as e:
        logger.warning("对话滚动压缩失败（本次不压缩，保留原文）: %s", e)


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
    # 历史问答沉淀参考开关（关闭后不检索、不注入沉淀，省 token）
    try:
        from core.utils import load_chat_flags
        qa_ref_enabled = bool(load_chat_flags().get("enable_qa_sediment_ref", True))
    except Exception:
        qa_ref_enabled = True

    # =============== [NEW] 意图路由：选子流程（Langflow 路由思想） ===============
    # 规则优先 + LLM 兜底，任何失败都降级 other；other 不在 FLOWS，走原 RAG 万能链路
    route = classify(user_msg, messages[-4:])
    intent = route["intent"]
    flow = FLOWS.get(intent)
    is_flow = flow is not None
    rag_system_prompt, query_vec = "", None

    if is_flow:
        # ---- 子流程：专用系统提示词 + 数据预取节点 + 工具子集 ----
        system_content = flow["system_prompt"]
        try:
            ctx = flow["data_prep"](user_msg, route.get("params") or {})
        except Exception as e:
            logger.exception("子流程 %s 数据预取失败（继续用基础提示词回答）: %s", intent, e)
            ctx = ""
        if ctx:
            system_content = f"{system_content}\n\n{ctx}"
        flow_tools = [t for t in AGENT_TOOLS if t["function"]["name"] in (flow.get("tools") or [])]
    else:
        # =============== 原 RAG 全链路（other 兜底，pgvector 未就绪也保证能答） ===============
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
            if qa_ref_enabled:
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
            # 旧 qa_logs.jsonl 的沉淀（历史问答；开关关闭时跳过）
            if qa_ref_enabled:
                qa_ref = format_qa_ref(retrieve_qa(user_msg, top_k=2))
                if qa_ref:
                    system_content = f"{system_content}\n\n{qa_ref}"
            # 最近上传文件摘要
            recent_ctx = _recent_upload_context()
            if recent_ctx:
                system_content = f"{system_content}\n\n{recent_ctx}"
        flow_tools = AGENT_TOOLS

    # ---- function calling 使用提示（仅工具启用时追加；子流程只提示其工具子集）----
    tools_enabled = bool(AGENT_TOOLS) and bool(config.get("chat.enable_tools", True))
    if tools_enabled:
        if is_flow and flow_tools:
            system_content += (
                "\n\n你可以调用工具获取最新数据（" + " / ".join(
                    t["function"]["name"] for t in flow_tools) + "）。"
                "工具返回结果优先于系统上下文中的旧数据；查不到就如实说明缺少哪部分数据，严禁编造。"
                "任务需要多类数据时，请先规划步骤：想清楚需要哪些数据、按什么顺序查询，"
                "再逐步调用工具；一次查不到就换条件再查，不要跳过查询直接编造。"
            )
        elif is_flow and not flow_tools:
            system_content += "\n\n请直接基于上方提供的数据回答，本次不需要调用工具。"
        else:
            system_content += (
                "\n\n你可以调用工具获取最新数据：需要查询具体学生/班级/实验成绩时调用 "
                "query_student / query_class_aggregate，涉及课程知识点时调用 search_knowledge_base。"
                "工具返回结果优先于系统上下文中的旧数据；查不到就如实说明缺少哪部分数据，严禁编造。"
                "任务需要多类数据时，请先规划步骤，逐步调用工具，不要跳过查询直接编造。"
            )
    tools_enabled = tools_enabled and bool(flow_tools)
    # 复杂子流程允许更多工具轮次（report/warning 等需要多步自主规划），其余用全局上限
    max_tool_rounds = _TOOL_MAX_ROUNDS
    if is_flow:
        try:
            max_tool_rounds = int(flow.get("max_tool_rounds") or _TOOL_MAX_ROUNDS)
        except (TypeError, ValueError):
            max_tool_rounds = _TOOL_MAX_ROUNDS

    built_messages: List[Dict[str, str]] = [{"role": "system", "content": system_content}]
    # ---- 记忆压缩：较早对话已滚成摘要，只注入最近窗口的原始消息 ----
    summary = conv.get("summary") or ""
    if summary:
        built_messages[0]["content"] += (
            f"\n\n## 更早对话摘要（较早问答已压缩，仅供背景参考，回答以最新数据为准）\n{summary}"
        )
    built_messages.extend(messages[-_RAW_HISTORY_WINDOW:])
    built_messages.append({"role": "user", "content": user_msg})

    messages.append({"role": "user", "content": user_msg})

    yield f"data: {json.dumps({'event': 'init', 'conversation_id': conv['id']}, ensure_ascii=False)}\n\n"

    reply_parts: List[str] = []
    try:
        if tools_enabled:
            # ---- function calling ReAct 循环：模型可多轮调用工具，最终给出回答 ----
            tool_round = 0
            while True:
                tool_round += 1
                tool_calls: Optional[List[Dict]] = None
                round_content: List[str] = []
                for item in chat_with_tools_stream(api_key, built_messages, flow_tools):
                    if isinstance(item, dict) and item.get("_tool_calls"):
                        tool_calls = item["_tool_calls"]
                    else:
                        round_content.append(str(item))
                        reply_parts.append(str(item))
                        yield f"data: {json.dumps({'event': 'delta', 'delta': str(item)}, ensure_ascii=False)}\n\n"
                if not tool_calls:
                    break
                if tool_round >= max_tool_rounds:
                    logger.warning("工具调用轮次达上限 %d，强制收尾回答", max_tool_rounds)
                    built_messages.append({"role": "system", "content": "已达最大工具调用轮次，请直接基于当前掌握的信息回答，不要再调用工具。"})
                    for chunk in chat_with_deepseek_stream(api_key, built_messages):
                        reply_parts.append(chunk)
                        yield f"data: {json.dumps({'event': 'delta', 'delta': chunk}, ensure_ascii=False)}\n\n"
                    break
                # 把本轮 assistant 的 tool_calls 原样追加，再执行工具并以 role="tool" 回填
                built_messages.append({
                    "role": "assistant", "content": "".join(round_content),
                    "tool_calls": tool_calls,
                })
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    name = fn.get("name") or ""
                    args = safe_json_loads(fn.get("arguments"))
                    result = execute_tool(name, args)
                    logger.info("工具调用：%s(%s) -> %d 字", name, json.dumps(args, ensure_ascii=False), len(result))
                    built_messages.append({
                        "role": "tool", "tool_call_id": tc.get("id", ""), "content": result,
                    })
        else:
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
    # ---- 记忆压缩：消息攒够后把最早一批滚入摘要（失败则保留原文下次再试）----
    _roll_summary(conv, api_key)
    save_conversation(conv)

    # ---- [NEW] 报告类：把 AI 生成的 Markdown 报告转 Word 供老师下载 ----
    # 事件放在 done 之前：前端收到 doc 事件后，在 done 时把下载信息挂到本条回复上
    if intent == "report" and reply.strip():
        try:
            doc_info = generate_report_docx(reply, conv.get("title") or "教学报告")
        except Exception as e:
            logger.warning("报告 Word 生成失败（跳过下载）: %s", e)
            doc_info = {}
        if doc_info:
            yield f"data: {json.dumps({'event': 'doc', 'url_path': doc_info['url_path'], 'filename': doc_info['filename']}, ensure_ascii=False)}\n\n"

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


def count_qa_sediment() -> Dict[str, int]:
    """问答沉淀各渠道条数（jsonl 旧库 + pgvector 新库），供设置页展示。"""
    out = {"jsonl": 0, "pgvector": 0}
    try:
        out["jsonl"] = count_qa()
    except Exception as e:
        logger.warning("统计 jsonl 沉淀条数失败: %s", e)
    try:
        from core.db import pg_store
        out["pgvector"] = pg_store.count_qa()
    except Exception as e:
        logger.warning("统计 pgvector 沉淀条数失败: %s", e)
    return out


def clear_qa_sediment() -> Dict[str, Any]:
    """清空全部问答沉淀（jsonl + pgvector），返回各渠道删除条数。
    任一渠道失败只记日志不影响另一渠道；都不抛异常。"""
    out = {"jsonl": 0, "pgvector": 0}
    try:
        from analysis.qa_sediment import clear_qa as clear_jsonl
        out["jsonl"] = clear_jsonl()
    except Exception as e:
        logger.warning("清空 jsonl 问答沉淀失败: %s", e)
    try:
        from core.db import pg_store
        out["pgvector"] = pg_store.clear_qa()
    except Exception as e:
        logger.warning("清空 pgvector 问答沉淀失败: %s", e)
    return out


def list_qa_sediment(limit: int = 100) -> Dict[str, Any]:
    """合并两库沉淀列表（jsonl 旧库 + pgvector 新库），带 source 字段便于逐条删除。
    按时间倒序返回。注意：两库 id 体系不同（jsonl 为 uuid 片段，pgvector 为表自增 id），
    删除时需按 source 分流。"""
    items: List[Dict[str, Any]] = []
    try:
        for rec in load_qa_logs(limit=limit):
            items.append({
                "source": "jsonl",
                "id": str(rec.get("id") or ""),
                "question": str(rec.get("question") or ""),
                "answer": str(rec.get("answer") or ""),
                "time": str(rec.get("time") or ""),
                "hit_knowledge": rec.get("hit_knowledge") or [],
            })
    except Exception as e:
        logger.warning("读取 jsonl 沉淀列表失败: %s", e)
    try:
        from core.db import pg_store
        for rec in pg_store.list_qa(limit=limit):
            items.append({
                "source": "pgvector",
                "id": str(rec.get("id") or ""),
                "question": str(rec.get("user_question") or ""),
                "answer": str(rec.get("assistant_reply") or ""),
                "time": str(rec.get("created_at") or ""),
                "hit_knowledge": rec.get("hit_knowledge") or [],
            })
    except Exception as e:
        logger.warning("读取 pgvector 沉淀列表失败: %s", e)
    items.sort(key=lambda x: str(x.get("time") or ""), reverse=True)
    return {"total": len(items), "logs": items}


def delete_qa_sediment(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """按 source 分流逐条删除沉淀，返回各渠道删除条数。不抛异常。"""
    out = {"jsonl": 0, "pgvector": 0}
    jsonl_ids = [i.get("id") for i in items if i.get("source") == "jsonl" and i.get("id")]
    pg_ids = [i.get("id") for i in items if i.get("source") == "pgvector" and i.get("id")]
    if jsonl_ids:
        try:
            from analysis.qa_sediment import delete_qa_by_ids as del_jsonl
            out["jsonl"] = del_jsonl(set(jsonl_ids))
        except Exception as e:
            logger.warning("删除 jsonl 沉淀失败: %s", e)
    if pg_ids:
        try:
            from core.db import pg_store
            out["pgvector"] = pg_store.delete_qa_by_ids(pg_ids)
        except Exception as e:
            logger.warning("删除 pgvector 沉淀失败: %s", e)
    return out
