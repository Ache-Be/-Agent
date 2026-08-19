# _*_ coding : UTF-8 _*_
"""
问题意图路由器：规则优先 + LLM 分类器兜底。

Langflow 思想映射：
- 路由器（ConditionalRouter）是一个独立节点：输入用户问题，输出 intent + confidence；
- 规则分类零成本，覆盖教师高频固定问法（学号/姓名/班级/触发词）；
- 规则命中不了时才调一次 LLM 分类器（temperature=0，只输出 JSON，带最近对话参考）；
- 兜底铁律：分类器解析失败、confidence < 0.6、网络异常 → 一律降级 other（万能链路），绝不抛出。
"""
import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger

from core.utils import load_api_key

# 支持的意图（与 chat_flows.FLOWS 一一对应）
INTENTS = (
    "score_student", "score_class", "score_trend",
    "knowledge_gap", "warning", "attendance", "report", "other",
)

# ---- 触发词表：注意优先级与交叉误判（"薄弱学生"不应命中知识点类）----
_TREND_WORDS = ("退步", "进步", "提升", "下降", "上升", "下滑", "回升", "趋势",
                "波动", "变化", "对比", "比较", "提高", "降低", "涨", "跌")
_WARNING_WORDS = ("预警", "风险", "重点关注", "挂科", "危险")
_KNOWLEDGE_WORDS = ("知识点", "错题", "没掌握", "薄弱点", "难点", "错在", "不会做",
                    "掌握得不好", "薄弱知识点", "学得不好", "没学会")
_ATTENDANCE_WORDS = ("考勤", "缺勤", "迟到", "早退", "旷课", "出勤", "点名")
_REPORT_WORDS = ("报告", "文档", "word", "Word", "汇报", "写一份", "总结报告",
                 "家长会", "生成一份", "写一个")

# 裸趋势词：出现即需要跨期对比（即便没抽到实体，也交给 LLM 二次判断）
_TREND_BARE = ("趋势", "对比", "比较")

# score_student 的"成绩语义"触发词：只有 name 且带这些词才可靠判个人成绩，
# 防止弱姓名抽取把"你好/两个/面向对象"等闲聊误当学生姓名
_SCORE_STUDENT_SEMANTIC = ("成绩", "分数", "多少分", "考", "排名", "及格", "不及格",
                           "薄弱", "退步", "进步", "学得", "表现", "怎么样", "如何", "实验")

# 强前缀姓名：同学X / 学生X / 姓名:X，可靠度高
_STRONG_NAME_RE = re.compile(r"同学([\u4e00-\u9fff]{2,4})|学生\s*([\u4e00-\u9fff]{2,4})|姓名[:：]?\s*([\u4e00-\u9fff]{2,4})")


def _has_strong_name(msg: str) -> bool:
    m = _STRONG_NAME_RE.search(msg)
    return bool(m and any(m.groups()))

_CLASSIFIER_PROMPT = """你是问题分类器。判断教师的问题属于哪一类，只输出 JSON：
{"intent": "...", "confidence": 0.0-1.0}

可选 intent：
- score_student: 问单个/几个学生个人的成绩、分数、排名、学得怎么样（有具体学生主体）
- score_class: 问班级整体成绩、平均分、及格率、班级对比、某实验各班级情况
- score_trend: 问成绩进步/退步、趋势、前后对比、波动变化
- knowledge_gap: 问哪些知识点没掌握、错题规律、薄弱知识点
- warning: 问要不要预警、哪些学生有风险、谁需要重点关注、挂科风险
- attendance: 问考勤、缺勤、迟到、出勤情况
- report: 要求生成/编写文档、报告（家长会报告、学习情况报告等）
- other: 都不像，或不确定

示例：
"张三数学这次考了多少分" -> {"intent":"score_student","confidence":0.95}
"帮我写一份家长会报告" -> {"intent":"report","confidence":0.9}
"这个班哪些学生需要重点关注" -> {"intent":"warning","confidence":0.9}
"张三成绩退步了吗" -> {"intent":"score_trend","confidence":0.85}
"软件1班平均分多少" -> {"intent":"score_class","confidence":0.95}
"面向对象的知识点有哪些" -> {"intent":"knowledge_gap","confidence":0.7}

只输出 JSON，不要其他文字。"""


def classify_by_rules(user_msg: str) -> Optional[str]:
    """纯规则分类（零成本）。返回 intent；规则拿不准返回 None 交给 LLM 兜底。"""
    msg = (user_msg or "").strip()
    if not msg:
        return None
    from services.rag_service import _extract_intent
    intent = _extract_intent(msg)
    sid = intent.get("student_id")
    name = intent.get("name")
    cls = intent.get("class_name")
    exp = intent.get("experiment_name")
    has_student = bool(sid or name)
    has_entity = bool(sid or name or cls or exp)

    # 优先级从高到低：文档 / 考勤 / 预警 / 知识点 / 趋势 / 个人 / 班级
    if any(w in msg for w in _REPORT_WORDS):
        return "report"
    if any(w in msg for w in _ATTENDANCE_WORDS):
        return "attendance"
    if any(w in msg for w in _WARNING_WORDS):
        return "warning"
    if any(w in msg for w in _KNOWLEDGE_WORDS):
        return "knowledge_gap"
    # 趋势：裸趋势词直接判；带主体趋势词也判（"张三退步了吗"/"这个班成绩下降了"）
    if any(w in msg for w in _TREND_BARE) or (any(w in msg for w in _TREND_WORDS) and has_entity):
        return "score_trend"
    if has_student:
        # 学号几乎总指具体学生；强前缀姓名可靠；否则需要姓名 + 成绩语义，
        # 防止弱姓名抽取把"你好/两个/面向对象"等闲聊误当学生姓名
        if sid or _has_strong_name(msg) or any(w in msg for w in _SCORE_STUDENT_SEMANTIC):
            return "score_student"
    if cls or exp or intent.get("is_agg_intent"):
        return "score_class"
    return None


def _llm_classify(user_msg: str, history: Optional[List[Dict[str, str]]] = None) -> Optional[Dict[str, Any]]:
    """LLM 分类器兜底：一次非流式小调用，temperature=0。失败返回 None（不抛出）。"""
    api_key = load_api_key()
    if not api_key:
        return None
    import requests

    messages: List[Dict[str, str]] = [{"role": "system", "content": _CLASSIFIER_PROMPT}]
    if history:
        for m in history[-2:]:
            role = str(m.get("role") or "user")
            if role not in ("user", "assistant"):
                role = "user"
            messages.append({"role": role, "content": str(m.get("content") or "")[:500]})
    messages.append({"role": "user", "content": f"问题：{user_msg}"})
    try:
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": messages, "stream": False,
                  "temperature": 0, "max_tokens": 64},
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning("分类器调用失败 %s: %s", resp.status_code, resp.text[:200])
            return None
        content = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""
        return _parse_classifier_json(content)
    except Exception as e:
        logger.warning("分类器调用异常: %s", e)
        return None


def _parse_classifier_json(content: str) -> Optional[Dict[str, Any]]:
    """从模型输出里提取 JSON。解析失败返回 None（兜底降级 other，不崩溃）。"""
    if not content:
        return None
    try:
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            return None
        obj = json.loads(content[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    intent = str(obj.get("intent") or "").strip()
    if intent not in INTENTS:
        return None
    try:
        conf = float(obj.get("confidence", 0))
    except (TypeError, ValueError):
        conf = 0.0
    return {"intent": intent, "confidence": min(max(conf, 0.0), 1.0)}


def classify(user_msg: str, history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """
    路由主入口。返回：
      {"intent": str, "confidence": float, "source": "rule"|"llm"|"fallback", "params": {...}}
    params 为 rag_service._extract_intent 抽取的结构化实体（学号/姓名/班级/实验/分数区间）。
    任何一步失败都降级 other，绝不抛出。
    """
    params: Dict[str, Any] = {}
    try:
        from services.rag_service import _extract_intent
        params = _extract_intent(user_msg or "")
    except Exception as e:
        logger.warning("意图实体抽取失败（不影响路由）: %s", e)

    rule = classify_by_rules(user_msg)
    if rule:
        return {"intent": rule, "confidence": 1.0, "source": "rule", "params": params}

    res = _llm_classify(user_msg, history)
    if res and res["confidence"] >= 0.6 and res["intent"] in INTENTS:
        return {"intent": res["intent"], "confidence": res["confidence"], "source": "llm", "params": params}
    return {"intent": "other", "confidence": 0.0, "source": "fallback", "params": params}
