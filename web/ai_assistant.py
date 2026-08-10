"""
DeepSeek AI 教学助手封装

提供：
  1. build_context() — 将分析结果转成给 AI 的上下文文本
  2. chat_with_deepseek() — 调用 DeepSeek API 返回流式/完整回复
"""

import json
import re
import requests
from typing import Optional, List, Dict


DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_MODEL = "deepseek-chat"

SYSTEM_PROMPT = """你是一位教学预警系统的 AI 教学助手，职责是帮助教师分析学生实验数据。

## 你的能力
1. 根据提供的分析数据回答关于学生薄弱项的问题
2. 给出针对性的学习建议和教学改进方案
3. 解释知识点之间的关系和优先级
4. 基于上下文中的『学生成绩预测』回答成绩预测/预警类问题

## 规则
- 回答以数据为依据，不要编造数据
- 涉及具体学生时，引用其薄弱知识点和得分率
- 建议要具体可操作，不要泛泛而谈
- 如果用户问的数据不在上下文中，请如实告知
- 使用中文回答，简洁清晰
- 回答学生成绩预测类问题时，必须使用『学生成绩预测』小节中的数据，并注明
  『这是基于历史趋势的统计估计，不是真实成绩，仅供教学预警参考』；预测区间
  要给出（如"预计 45~55 分"），不确定时如实说明样本不足"""


_CN_UNIT_RE = re.compile(r"第(.+?)单元")
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _unit_short(name: str) -> str:
    """'第一单元练习' → '一'"""
    m = _CN_UNIT_RE.search(name or "")
    return m.group(1).strip() if m else (name or "")


def _unit_sort_key(name: str):
    m = _CN_UNIT_RE.search(name or "")
    if not m:
        return (1, name or "")
    return (0, _CN_NUM.get(m.group(1), 99))


def _fmt_unit_line(s: Dict) -> Optional[str]:
    """单个学生的单元练习 → 紧凑文本行（成绩→完成率，未提交单独标注）"""
    units = s.get("units") or []
    segs = []
    for u in units:
        mx = u.get("max") or 0
        if mx <= 0:
            continue
        rate = (u.get("score") or 0) / mx
        if rate <= 0:
            segs.append(f"{_unit_short(u.get('name', ''))}未交")
        else:
            segs.append(f"{_unit_short(u.get('name', ''))}{rate * 100:.0f}%")
    exam = s.get("exam")
    if exam and (exam.get("max") or 0) > 0:
        er = (exam.get("score") or 0) / exam["max"]
        segs.append(f"期末{er * 100:.0f}%" if er > 0 else "期末未交")
    act = s.get("activity_score")
    if act is not None:
        segs.append(f"课堂活动{act:g}")
    if not segs:
        return None
    return f"- {s.get('name', '')}（{s.get('student_id', '')}）{' '.join(segs)}"


def _fmt_attendance_line(s: Dict) -> str:
    """单个学生的课堂活动明细 → 紧凑文本行"""
    segs = []
    sign, pub = s.get("sign_count"), s.get("publish_count")
    if sign is not None and pub is not None:
        segs.append(f"签到 {sign:g}/{pub:g}")
    sr = s.get("signed_ratio")
    if sr is not None:
        segs.append(f"出勤率{sr * 100:.0f}%")
    tr = s.get("test_ratio")
    if tr is not None:
        segs.append(f"作答率{tr * 100:.0f}%")
    ta = s.get("test_accuracy")
    if ta is not None:
        segs.append(f"正确率{ta * 100:.0f}%")
    at = s.get("activity_total")
    if at is not None:
        segs.append(f"活动总分{at:g}")
    return f"- {s.get('name', '')}（{s.get('student_id', '')}）{'，'.join(segs)}"


def build_analysis_context(
    student_list: List[Dict],
    top_error: List[Dict],
    total_students: int,
    weak_count: int,
    experiment_count: int,
    quiz_count: int = 0,
    unit_results: Optional[List[Dict]] = None,
    attendance_results: Optional[List[Dict]] = None,
    prediction_text: str = "",
) -> str:
    """将分析结果构建成 AI 上下文文本"""
    unit_results = unit_results or []
    attendance_results = attendance_results or []
    prediction_text = prediction_text or ""
    parts = [f"## 当前分析概况\n共有 {experiment_count} 个实验，{total_students} 名学生，其中 {weak_count} 人有薄弱项"]
    extra_sets = []
    if quiz_count:
        extra_sets.append(f"{quiz_count} 次随堂测验（章节级）")
    if unit_results:
        extra_sets.append(f"{len(unit_results)} 份单元练习（含期末综合测试）")
    if attendance_results:
        extra_sets.append(f"{len(attendance_results)} 份课堂活动分数明细")
    if extra_sets:
        parts[0] += "，另有 " + "、".join(extra_sets) + " 数据。"
    else:
        parts[0] += "，但未包含随堂测验、单元练习、课堂活动等补充数据。"
    if experiment_count == 0:
        parts[0] += "（当前未包含头歌实验数据，实验级分析不可用）"
    parts[0] += "\n"

    # 数据范围约束：缺失的数据类型要如实告知，不能编造
    limits = []
    if experiment_count == 0:
        limits.append(
            "实验任务得分数据（头歌实验 CSV）：缺少后无法提供实验表现、子任务级得分与薄弱项、"
            "实验正确率等分析；若用户询问这类问题，请如实告知并建议上传头歌实验 CSV（选择『合并』追加）。"
        )
    if quiz_count == 0:
        limits.append(
            "随堂测验数据（章节级 xlsx）：缺少后无法提供某章节正确率、薄弱章节等分析；"
            "若用户询问这类问题，请如实告知并建议上传随堂测验 xlsx（选择『合并』追加）。"
        )
    if not unit_results:
        limits.append(
            "单元练习成绩（如『苏理工软件(单元练习).xlsx』）：缺少后无法提供各单元练习完成率、"
            "期末综合测试、课程活动总分等分析；若用户询问这类问题，请如实告知并建议上传单元练习 xlsx。"
        )
    if not attendance_results:
        limits.append(
            "课堂活动分数明细（如『课堂活动分数明细-苏理工软件.xlsx』）：缺少后无法提供签到出勤率、"
            "随堂测试作答/正确率、课堂活动总分等分析；若用户询问这类问题，请如实告知并建议上传对应 xlsx。"
        )
    if limits:
        parts.append(
            "\n## 数据范围限制（必须遵守）\n当前分析结果中缺少：\n- "
            + "\n- ".join(limits)
            + "\n严禁凭空编造不存在的学生数据。\n"
        )

    if top_error:
        parts.append("\n## 知识点易错排行（TOP 10）")
        for i, te in enumerate(top_error[:10], 1):
            rate = te.get("error_rate", 0)
            parts.append(f"{i}. {te['name']}（{te.get('unit','')}）— 易错人数 {te.get('error_count',0)}，易错率 {rate*100 if rate < 1 else rate}%")

    # 未上传实验/测验时 student_list 为空，但单元练习/课堂活动里也有学生名单，仍要渲染数据块
    if student_list or unit_results or attendance_results:
        weak_students = [s for s in student_list if s.get("weak_count", 0) > 0]
        if weak_students:
            parts.append(f"\n## 有薄弱项的学生（共 {len(weak_students)} 人）")
            for s in weak_students[:20]:
                kn = s.get("weak_knowledge_names", "")
                rate = s.get("weak_rate", 0)
                parts.append(f"- {s['name']}（学号 {s.get('student_id','')}）薄弱子任务 {s.get('weak_count',0)} 个，薄弱率 {rate*100 if rate < 1 else rate}%")
                if kn:
                    parts.append(f"  薄弱知识点：{kn[:120]}")
            if len(weak_students) > 20:
                parts.append(f"  ...还有 {len(weak_students) - 20} 名学生")

        # 随堂测验章节薄弱
        quiz_weak_students = [s for s in student_list if s.get("quiz_weak_chapters")]
        if quiz_weak_students:
            parts.append(f"\n## 随堂测验章节薄弱学生（共 {len(quiz_weak_students)} 人）")
            for s in quiz_weak_students[:20]:
                chapters = "、".join(s.get("quiz_weak_chapters", []))
                avg = s.get("quiz_avg_accuracy", 0)
                parts.append(f"- {s['name']}（学号 {s.get('student_id','')}）测验平均正确率 {avg*100:.0f}%，薄弱章节：{chapters}")
            if len(quiz_weak_students) > 20:
                parts.append(f"  ...还有 {len(quiz_weak_students) - 20} 名学生")

        # 单元练习与期末测试数据
        if unit_results:
            parts.append("\n## 单元练习与期末测试数据")
            uclasses = "、".join(sorted({u.get("class_name", "") or "" for u in unit_results}))
            ustu = sum(u.get("student_count", 0) for u in unit_results)
            parts.append(f"来源：{len(unit_results)} 份文件（班级：{uclasses}），共 {ustu} 名学生。")
            unit_agg = {}
            exam_rates = []
            for ures in unit_results:
                for s in ures.get("students", []):
                    for u in s.get("units", []):
                        mx = u.get("max") or 0
                        if mx > 0:
                            unit_agg.setdefault(u.get("name", ""), []).append((u.get("score") or 0) / mx)
                    ex = s.get("exam")
                    if ex and (ex.get("max") or 0) > 0:
                        exam_rates.append((ex.get("score") or 0) / ex["max"])
            if unit_agg:
                cls_lines = []
                for uname in sorted(unit_agg.keys(), key=_unit_sort_key):
                    rates = unit_agg[uname]
                    avg = sum(rates) / len(rates)
                    sub = sum(1 for r in rates if r > 0)
                    cls_lines.append(f"{_unit_short(uname)}：提交 {sub}/{len(rates)}，平均完成率 {avg * 100:.0f}%")
                if exam_rates:
                    cls_lines.append(f"期末综合测试：平均完成率 {sum(exam_rates) / len(exam_rates) * 100:.0f}%（{len(exam_rates)} 人）")
                parts.append("班级整体：\n- " + "\n- ".join(cls_lines))
            parts.append(f"学生明细（{ustu} 人）：")
            for ures in unit_results:
                for s in ures.get("students", []):
                    line = _fmt_unit_line(s)
                    if line:
                        parts.append(line)

        # 课堂活动分数明细数据
        if attendance_results:
            parts.append("\n## 课堂活动分数明细数据")
            aclasses = "、".join(sorted({a.get("class_name", "") or "" for a in attendance_results}))
            astu = sum(a.get("student_count", 0) for a in attendance_results)
            parts.append(f"来源：{len(attendance_results)} 份文件（班级：{aclasses}），共 {astu} 名学生。")
            sr_vals, ar_vals, ta_vals, act_vals = [], [], [], []
            for ares in attendance_results:
                for s in ares.get("students", []):
                    if s.get("signed_ratio") is not None:
                        sr_vals.append(s["signed_ratio"])
                    if s.get("test_ratio") is not None:
                        ar_vals.append(s["test_ratio"])
                    if s.get("test_accuracy") is not None:
                        ta_vals.append(s["test_accuracy"])
                    if s.get("activity_total") is not None:
                        act_vals.append(s["activity_total"])
            cls_parts = []
            if sr_vals:
                cls_parts.append(f"平均签到出勤率 {sum(sr_vals) / len(sr_vals) * 100:.0f}%")
            if ar_vals:
                cls_parts.append(f"平均作答率 {sum(ar_vals) / len(ar_vals) * 100:.0f}%")
            if ta_vals:
                cls_parts.append(f"平均测试正确率 {sum(ta_vals) / len(ta_vals) * 100:.0f}%")
            if act_vals:
                cls_parts.append(f"平均课堂活动总分 {sum(act_vals) / len(act_vals):.1f}")
            if cls_parts:
                parts.append("班级整体：" + "，".join(cls_parts))
            parts.append(f"学生明细（{astu} 人）：")
            for ares in attendance_results:
                for s in ares.get("students", []):
                    parts.append(_fmt_attendance_line(s))

        # 全部学生名单（供用户核对名单、点名使用；学生信息属于教学分析数据，可直接列出）
        # 未上传实验/测验时，用单元练习/课堂活动中的名单兜底
        if not student_list:
            roster = {}
            for ures in unit_results:
                for s in ures.get("students", []):
                    roster.setdefault(s.get("student_id") or s.get("name"), s)
            for ares in attendance_results:
                for s in ares.get("students", []):
                    roster.setdefault(s.get("student_id") or s.get("name"), s)
            student_list = list(roster.values())
        parts.append(f"\n## 全部学生名单（共 {len(student_list)} 人）")
        for s in student_list:
            sid = s.get("student_id", "")
            line = f"- {s.get('name', '')}"
            if sid:
                line += f"（学号 {sid}）"
            if s.get("weak_count", 0) > 0:
                line += f" [薄弱]"
            parts.append(line)

    # 学生成绩预测（统计估计，仅供预警参考）
    if prediction_text:
        parts.append(prediction_text)

    return "\n".join(parts)


# ============ 轻量 RAG：知识点库检索 ============

_KB_FIELDS = ("教学层次", "知识领域", "MOOC教学单元", "项目名称", "视频/知识点名称", "视频时长")


# 向量化检索索引缓存（知识库内容不变时复用，避免重复构建）
_index_cache_key = None
_index_cache = None


def _get_kb_index(knowledge_base: List[Dict]):
    """按知识库内容构建（带缓存）向量索引"""
    global _index_cache_key, _index_cache
    from analysis.vector_search import build_knowledge_index

    key = tuple(sorted(str(r.get("视频/知识点名称") or "") for r in knowledge_base))
    if key != _index_cache_key or _index_cache is None:
        _index_cache = build_knowledge_index(knowledge_base)
        _index_cache_key = key
    return _index_cache


def retrieve_knowledge(query: str, knowledge_base: List[Dict], top_k: int = 5) -> List[Dict]:
    """
    RAG 检索：从知识点库召回与问题最相关的条目（向量化语义匹配）。

    复用 analysis.vector_search（字符 n-gram + TF-IDF + 余弦），与主流程的
    题目/子任务匹配共用同一套引擎，避免两套检索逻辑结果不一致。
    分数低于阈值时视为无关问题，返回空列表（避免向 AI 注入噪音知识）。
    """
    if not knowledge_base or not query:
        return []
    from analysis.vector_search import search_semantic

    # 阈值 0.25：对话注入对召回要求低于精确度，避免无关问题带入噪音知识
    index = _get_kb_index(knowledge_base)
    scored = search_semantic(query, index, top_k=top_k, threshold=0.25)
    return [entry for _, entry in scored]


def format_knowledge_ref(records: List[Dict]) -> str:
    """把检索到的知识点条目格式化为注入 prompt 的参考文本"""
    if not records:
        return ""
    lines = [
        "\n## 与本问题相关的课程知识点（知识库检索结果，供参考）\n"
        "仅作为参考资料，涉及具体知识点内容时以知识库为准，不得编造知识库中不存在的知识点："
    ]
    for rec in records:
        name = str(rec.get("视频/知识点名称") or "").strip() or "（未命名知识点）"
        meta = " | ".join(
            str(rec.get(k) or "").strip()
            for k in _KB_FIELDS
            if k != "视频/知识点名称" and str(rec.get(k) or "").strip()
        )
        line = f"- {name}"
        if meta:
            line += f"（{meta}）"
        lines.append(line)
    return "\n".join(lines)


def chat_with_deepseek(
    api_key: str,
    messages: List[Dict],
    model: str = DEFAULT_MODEL,
    temperature: float = 0.7,
    stream: bool = False,
) -> Optional[str]:
    """调用 DeepSeek API 获取回复"""
    if not api_key:
        return "请先在右上角设置 DeepSeek API Key"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": stream,
    }

    try:
        resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=300)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except requests.exceptions.Timeout:
        return "请求超时，请检查网络连接后重试"
    except requests.exceptions.HTTPError as e:
        if resp.status_code == 401:
            return "API Key 无效，请检查后重新设置"
        return f"API 请求失败（{resp.status_code}）：{resp.text[:200]}"
    except Exception as e:
        return f"请求出错：{str(e)}"


def chat_with_deepseek_stream(
    api_key: str,
    messages: List[Dict],
    model: str = DEFAULT_MODEL,
    temperature: float = 0.7,
) -> Optional[str]:
    """流式调用 DeepSeek API，逐段 yield 回复增量文本。

    生成器每次 yield 一个字符串片段；出错时 yield 错误提示。
    """
    if not api_key:
        yield "请先在右上角设置 DeepSeek API Key"
        return

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }

    try:
        resp = requests.post(DEEPSEEK_API_URL, headers=headers,
                             json=payload, stream=True, timeout=300)
        if resp.status_code == 401:
            yield "API Key 无效，请检查后重新设置"
            return
        resp.raise_for_status()
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
                delta = obj["choices"][0]["delta"].get("content", "")
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
            if delta:
                yield delta
    except requests.exceptions.Timeout:
        yield "请求超时，请检查网络连接后重试"
    except requests.exceptions.HTTPError as e:
        yield f"API 请求失败（{resp.status_code}）：{resp.text[:200]}"
    except Exception as e:
        yield f"请求出错：{str(e)}"
