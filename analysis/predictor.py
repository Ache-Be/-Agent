"""
学生成绩预测模块（轻量统计预测，无第三方 ML 依赖）。

预测思路：
1. 成绩序列 = 跨时间点的成绩（实验得分率序列、随堂测验正确率序列、单元练习完成率序列）
2. 用最小二乘线性回归拟合趋势，预测下一个时间点的得分区间（预测值 ± 残差波动）
3. 结合当前水平、趋势方向、薄弱知识点数、出勤率 → 风险分级（高/中/低/平稳）

输出：可注入 AI 上下文的预测文本，AI 据此回答"某同学下次成绩如何、哪些学生需要
重点关注"。所有预测均为统计估计，只作教学预警参考，不替代真实成绩。
"""

from collections import defaultdict
from typing import Dict, List, Optional

from analysis.cross_analyzer import _unit_sort_key


# ---------- 趋势预测：最小二乘线性回归 ----------


def predict_next_score(series: List[float]) -> Optional[Dict]:
    """对百分制成绩序列做线性回归，预测下一个时间点得分及区间。

    返回:
        {
            "predicted": 预测值(0-100), "low": 下界, "high": 上界,
            "trend": "上升"/"下降"/"平稳", "slope": 每步变化,
            "last": 最近一次, "avg": 均值, "n": 样本数,
        }
        序列为空返回 None；样本不足 2 时退化为取最近一次。
    """
    series = [float(x) for x in series if x is not None]
    if not series:
        return None
    n = len(series)
    last = series[-1]
    avg = sum(series) / n
    if n < 2:
        return {
            "predicted": round(last, 1), "low": round(last, 1), "high": round(last, 1),
            "trend": "数据不足", "slope": 0.0, "last": round(last, 1),
            "avg": round(avg, 1), "n": n,
        }
    # 最小二乘: y = a + b*x（x = 0,1,2,...）
    xs = list(range(n))
    mx = sum(xs) / n
    my = avg
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, series))
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sxy / sxx if sxx else 0.0
    intercept = my - slope * mx
    pred = intercept + slope * n  # 下一个点
    # 训练点残差标准差 → 波动区间
    resid = [series[i] - (intercept + slope * i) for i in range(n)]
    sd = (sum(r * r for r in resid) / n) ** 0.5
    pred = max(0.0, min(100.0, pred))
    low = max(0.0, pred - sd)
    high = min(100.0, pred + sd)
    if slope > 1.0:
        trend = "上升"
    elif slope < -1.0:
        trend = "下降"
    else:
        trend = "平稳"
    return {
        "predicted": round(pred, 1), "low": round(low, 1), "high": round(high, 1),
        "trend": trend, "slope": round(slope, 2),
        "last": round(last, 1), "avg": round(avg, 1), "n": n,
    }


# ---------- 学生序列提取 ----------


def _experiment_series(profile: Dict) -> List[float]:
    """跨实验得分率序列：每个实验取平均得分率（百分制），按实验出现顺序。"""
    exp_rates = defaultdict(list)
    order = []
    for t in profile.get("tasks", []):
        exp = t.get("experiment", "")
        if exp not in exp_rates:
            order.append(exp)
        exp_rates[exp].append(t.get("rate", 0))
    return [sum(exp_rates[e]) / len(exp_rates[e]) * 100 for e in order]


def _quiz_series(profile: Dict) -> List[float]:
    """随堂测验正确率序列（百分制），跳过 0 值（未参与）。"""
    return [q.get("accuracy", 0) * 100 for q in profile.get("quiz_chapters", [])
            if q.get("accuracy", 0) > 0]


def _unit_series(unit_student: Dict) -> List[float]:
    """单元练习完成率序列（百分制）：按表头顺序（第一单元→…），期末综合测试追加在最后。"""
    series = []
    for u in unit_student.get("units", []):
        mx = u.get("max") or 0
        if mx > 0:
            series.append((u.get("score") or 0) / mx * 100)
    ex = unit_student.get("exam")
    if ex and (ex.get("max") or 0) > 0:
        series.append((ex.get("score") or 0) / ex["max"] * 100)
    return series


# ---------- 风险分级（规则打分，可解释） ----------


def assess_student_risk(
    profile: Dict,
    exp_pred: Optional[Dict] = None,
    quiz_pred: Optional[Dict] = None,
    unit_pred: Optional[Dict] = None,
    attendance_ratio: Optional[float] = None,
) -> Dict:
    """规则打分 → 风险等级。

    规则（可解释依据）：
      +2  实验平均得分率 < 60%
      +1  实验平均得分率 60%~70%
      +1  实验成绩呈下降趋势
      +1  随堂测验平均正确率 < 70%（有测验数据时）
      +1  薄弱知识点 >= 5 个
      +2  薄弱知识点 >= 2 个（在 >=5 不叠加，取较高档）
      +1  出勤率 < 70%（有出勤数据时）
    总分 >=3 高风险；>=2 中风险；>=1 低风险；0 平稳。
    """
    reasons = []
    score = 0
    tasks = profile.get("tasks", [])
    rates = [t.get("rate", 0) for t in tasks]
    if rates:
        avg_rate = sum(rates) / len(rates)
        if avg_rate < 0.6:
            score += 2
            reasons.append(f"实验平均得分率仅 {avg_rate * 100:.0f}%")
        elif avg_rate < 0.7:
            score += 1
            reasons.append(f"实验平均得分率 {avg_rate * 100:.0f}% 偏低")
        if exp_pred and exp_pred.get("trend") == "下降":
            score += 1
            reasons.append("实验成绩呈下降趋势")

    wk = profile.get("weak_knowledge_count", 0)
    if wk >= 5:
        score += 2
        reasons.append(f"薄弱知识点多达 {wk} 个")
    elif wk >= 2:
        score += 1
        reasons.append(f"薄弱知识点 {wk} 个")

    qa = profile.get("quiz_avg_accuracy", 0)
    if qa and qa < 0.7:
        score += 1
        reasons.append(f"随堂测验平均正确率 {qa * 100:.0f}%")

    if attendance_ratio is not None and attendance_ratio < 0.7:
        score += 1
        reasons.append(f"签到出勤率仅 {attendance_ratio * 100:.0f}%")

    if score >= 3:
        level = "高风险"
    elif score >= 2:
        level = "中风险"
    elif score >= 1:
        level = "低风险"
    else:
        level = "平稳"
    return {"level": level, "score": score, "reasons": reasons}


# ---------- 预测文本（注入 AI 上下文） ----------


def _fmt_seq(s: List[float]) -> str:
    """[85.0, 80.0, 76.0] → '85→80→76'"""
    return "→".join(f"{x:.0f}" for x in s)


def build_prediction_text(
    students_map: Dict,
    unit_results: Optional[List[Dict]] = None,
    attendance_results: Optional[List[Dict]] = None,
    top_high: int = 10,
) -> str:
    """为每个学生做趋势预测 + 风险分级，生成注入 AI 上下文的预测文本。

    参数:
        students_map: 跨实验聚合的完整学生字典（agg["students"]，含 tasks/quiz_chapters）
        unit_results: 单元练习结果列表（含 units/exam）
        attendance_results: 课堂活动结果列表（含 signed_ratio）
        top_high: 高风险学生详细展示的最大人数
    """
    unit_results = unit_results or []
    attendance_results = attendance_results or []

    # 学号 → 单元练习学生 / 出勤率（unit/attendance 用学号或姓名关联）
    unit_stu_map = {}
    for ures in unit_results:
        for s in ures.get("students", []):
            sid = s.get("student_id") or s.get("name")
            if sid:
                unit_stu_map.setdefault(sid, s)
    attend_map = {}
    for ares in attendance_results:
        for s in ares.get("students", []):
            sid = s.get("student_id") or s.get("name")
            if sid:
                attend_map.setdefault(sid, s.get("signed_ratio"))

    if not students_map:
        return ""

    profiles = []
    for sid, profile in students_map.items():
        name = profile.get("name", "")
        if not name:
            continue
        exp_series = _experiment_series(profile)
        exp_pred = predict_next_score(exp_series)
        quiz_pred = predict_next_score(_quiz_series(profile))
        us = unit_stu_map.get(sid) or unit_stu_map.get(name)
        unit_pred = predict_next_score(_unit_series(us)) if us else None
        att = attend_map.get(sid)
        if att is None:
            att = attend_map.get(name)
        risk = assess_student_risk(profile, exp_pred, quiz_pred, unit_pred, att)
        profiles.append({
            "name": name,
            "student_id": profile.get("student_id", ""),
            "risk": risk,
            "exp": exp_pred,
            "quiz": quiz_pred,
            "unit": unit_pred,
            "attendance": att,
            "exp_series": exp_series,
            "quiz_avg": profile.get("quiz_avg_accuracy", 0),
        })

    if not profiles:
        return ""

    levels = {"高风险": [], "中风险": [], "低风险": [], "平稳": []}
    for p in profiles:
        levels[p["risk"]["level"]].append(p)

    lines = [
        "\n## 学生成绩预测（基于历史成绩趋势的统计估计，仅供教学预警参考，非真实成绩）",
        f"共 {len(profiles)} 名学生纳入预测：高风险 {len(levels['高风险'])} 人、"
        f"中风险 {len(levels['中风险'])} 人、低风险 {len(levels['低风险'])} 人、平稳 {len(levels['平稳'])} 人。",
        "预测区间为估计值，可能不准确；回答学生成绩类问题时必须注明『这是基于历史趋势的预测，不是真实成绩』。",
    ]

    # 高风险详情
    high = sorted(levels["高风险"], key=lambda p: -p["risk"]["score"])[:top_high]
    if high:
        lines.append("\n【高风险·重点预警】")
        for p in high:
            r = p["risk"]
            det = []
            if p["exp"]:
                det.append(
                    f"实验得分率 {_fmt_seq(p['exp_series'])}（{p['exp']['trend']}），"
                    f"预测下次约 {p['exp']['low']:.0f}~{p['exp']['high']:.0f} 分"
                )
            if p["quiz"]:
                qa = p.get("quiz_avg") or 0
                det.append(f"随堂测验趋势：{p['quiz']['trend']}"
                           + (f"，平均正确率 {qa * 100:.0f}%" if qa > 0 else ""))
            if p["unit"]:
                det.append(f"单元练习/期末趋势：{p['unit']['trend']}（最近 {p['unit']['last']:.0f} 分）")
            if p["attendance"] is not None:
                det.append(f"签到出勤率 {p['attendance'] * 100:.0f}%")
            reason_txt = "；".join(r["reasons"]) or "多项指标偏低"
            lines.append(f"- {p['name']}（学号 {p['student_id']}）：风险【{r['level']}】")
            for d in det:
                lines.append(f"  · {d}")
            lines.append(f"  依据：{reason_txt}")
        if len(levels["高风险"]) > top_high:
            lines.append(f"  …另有 {len(levels['高风险']) - top_high} 名高风险学生（名单见『全部学生名单』）")

    # 中风险简述
    mid = levels["中风险"]
    if mid:
        lines.append("\n【中风险·需关注】")
        briefs = []
        for p in mid[:15]:
            brief = f"{p['name']}"
            if p["exp"] and p["exp"]["trend"] != "数据不足":
                brief += f"（实验{p['exp']['trend']}，预测 {p['exp']['low']:.0f}~{p['exp']['high']:.0f} 分）"
            briefs.append(brief)
        lines.append("、".join(briefs) + ("…" if len(mid) > 15 else ""))

    # 低风险简述
    low = levels["低风险"]
    if low:
        lines.append("\n【低风险·建议跟踪】")
        lines.append("、".join(p["name"] for p in low[:15]) + ("…" if len(low) > 15 else ""))

    if not (high or mid or low):
        lines.append("\n全体学生表现平稳，暂无重点预警对象。")

    return "\n".join(lines)
