"""
数据分析模块：读取学生答题数据，统计错题率，识别高频错题。
"""

import csv
from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Tuple


def load_student_scores(filepath: str) -> List[Dict]:
    """加载学生答题数据 CSV 文件"""
    records = []
    with open(filepath, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["correct"] = int(row["correct"])
            records.append(row)
    return records


def compute_question_stats(records: List[Dict]) -> List[Dict]:
    """
    按题目统计：总答题次数、正确次数、错误次数、错误率。
    返回按错误率降序排列的题目列表。
    """
    stats = defaultdict(lambda: {
        "question_id": "",
        "question_content": "",
        "subject": "",
        "chapter": "",
        "total": 0,
        "correct_count": 0,
        "wrong_count": 0,
    })

    for r in records:
        qid = r["question_id"]
        stats[qid]["question_id"] = qid
        stats[qid]["question_content"] = r["question_content"]
        stats[qid]["subject"] = r["subject"]
        stats[qid]["chapter"] = r["chapter"]
        stats[qid]["total"] += 1
        if r["correct"] == 1:
            stats[qid]["correct_count"] += 1
        else:
            stats[qid]["wrong_count"] += 1

    results = []
    for qid, s in stats.items():
        s["error_rate"] = round(s["wrong_count"] / s["total"], 2)
        results.append(s)

    results.sort(key=lambda x: x["error_rate"], reverse=True)
    return results


def compute_chapter_stats(question_stats: List[Dict]) -> List[Dict]:
    """按实验（章节）维度汇总：平均错误率、总错题数、需要重点关注的知识点数量"""
    chapters = defaultdict(lambda: {
        "chapter": "",
        "subject": "",
        "total_questions": 0,
        "total_wrong": 0,
        "high_error_questions": 0,  # 错误率 >= 60%
        "medium_error_questions": 0,  # 错误率 40% ~ 60%
    })

    for q in question_stats:
        ch = q["chapter"]
        chapters[ch]["chapter"] = ch
        chapters[ch]["subject"] = q["subject"]
        chapters[ch]["total_questions"] += 1
        chapters[ch]["total_wrong"] += q["wrong_count"]
        if q["error_rate"] >= 0.6:
            chapters[ch]["high_error_questions"] += 1
        elif q["error_rate"] >= 0.4:
            chapters[ch]["medium_error_questions"] += 1

    results = []
    for ch, s in chapters.items():
        s["avg_error_rate"] = round(
            s["total_wrong"] / (s["total_questions"] * s["total_questions"]), 2
        )
        results.append(s)

    return results


def identify_wrong_questions(
    question_stats: List[Dict], threshold: float = 0.5
) -> List[Dict]:
    """识别错题率超过阈值的题目（默认 50%）"""
    return [q for q in question_stats if q["error_rate"] >= threshold]


def generate_analysis_report(
    question_stats: List[Dict], chapter_stats: List[Dict], threshold: float = 0.5
) -> str:
    """生成可视化文本报告"""
    wrong_questions = identify_wrong_questions(question_stats, threshold)

    lines = []
    lines.append("=" * 64)
    lines.append("  学生实验数据 - 教学预警分析报告")
    lines.append("=" * 64)
    lines.append(f"生成时间：{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(
        f"总题数：{len(question_stats)}  |  "
        f"高频错题（错误率≥{int(threshold*100)}%）：{len(wrong_questions)} 道"
    )
    lines.append("")

    # ---- 章节汇总 ----
    lines.append("-" * 64)
    lines.append("【一、实验（章节）维度汇总】")
    lines.append("-" * 64)
    lines.append(f"{'实验名称':<24} {'学科':<6} {'题目数':<8} {'高危':<8} {'中危':<8}")
    lines.append("-" * 64)
    for ch in chapter_stats:
        lines.append(
            f"{ch['chapter']:<24} {ch['subject']:<6} "
            f"{ch['total_questions']:<8} {ch['high_error_questions']:<8} "
            f"{ch['medium_error_questions']:<8}"
        )
    lines.append("")

    # ---- 高频错题详情 ----
    lines.append("-" * 64)
    lines.append(f"【二、高频错题详情（错误率≥{int(threshold*100)}%）】")
    lines.append("-" * 64)
    lines.append(f"{'题号':<8} {'错误率':<8} {'正确/总':<12} {'题目内容'}")
    lines.append("-" * 64)
    for q in wrong_questions:
        lines.append(
            f"{q['question_id']:<8} {q['error_rate']:<8} "
            f"{q['correct_count']}/{q['total']:<10} {q['question_content']}"
        )
    lines.append("")

    # ---- 所有题目排行 ----
    lines.append("-" * 64)
    lines.append("【三、全部题目错率排行】")
    lines.append("-" * 64)
    lines.append(f"{'排名':<6} {'题号':<8} {'错误率':<8} {'章节':<24} {'题目内容'}")
    lines.append("-" * 64)
    for i, q in enumerate(question_stats, 1):
        lines.append(
            f"{i:<6} {q['question_id']:<8} {q['error_rate']:<8} "
            f"{q['chapter']:<24} {q['question_content']}"
        )
    lines.append("")

    return "\n".join(lines)


def export_wrong_questions_csv(
    wrong_questions: List[Dict], output_path: str, knowledge_map: Dict = None
):
    """导出高频错题到 CSV（可选带知识点映射）"""
    fieldnames = [
        "question_id", "question_content", "subject", "chapter",
        "total", "correct_count", "wrong_count", "error_rate",
        "knowledge_chapter", "knowledge_section", "knowledge_points",
    ]
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for q in wrong_questions:
            row = dict(q)
            row["error_rate"] = f"{q['error_rate']:.0%}"
            if knowledge_map and q["chapter"] in knowledge_map:
                kb = knowledge_map[q["chapter"]]
                row["knowledge_chapter"] = kb["chapter"]
                row["knowledge_section"] = kb["section"]
                row["knowledge_points"] = "; ".join(kb["key_points"])
            else:
                row["knowledge_chapter"] = ""
                row["knowledge_section"] = ""
                row["knowledge_points"] = ""
            writer.writerow(row)
