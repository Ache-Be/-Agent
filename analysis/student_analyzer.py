"""
学生级分析引擎。

对每个学生进行细致的薄弱知识点分析：
1. 遍历该学生所有子任务得分
2. 得分率低于阈值 → 标记该子任务关联的知识点为"薄弱"
3. 按知识点单元聚合，生成个人画像
"""

import csv
import re
from typing import List, Dict, Optional
from collections import defaultdict, Counter
from datetime import datetime

from analysis.subtask_mapper import build_experiment_subtask_mapping
from analysis.knowledge_builder import load_knowledge_base
from analysis.config import load_config

# 得分率低于此阈值视为薄弱（可在 web/config/settings.json 中调整）
WEAK_THRESHOLD = load_config()["weak_threshold"]

# 中文数字 → 整数
_CN_NUMS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _unit_sort_key(unit: str) -> tuple:
    """按教学单元编号排序"""
    m = re.match(r'第(.+)单元', unit)
    if m:
        cn = m.group(1)
        total = 0
        for c in cn:
            total = total * 10 + _CN_NUMS.get(c, 0)
        return (0, total)
    if unit.startswith("阶段"):
        return (1, unit)
    return (2, unit)


def analyze_student_weakness(
    student: Dict,
    subtask_mapping: Dict[str, dict],
    task_stats: Dict[str, dict],
) -> Dict:
    """
    分析单个学生的薄弱知识点。
    
    参数:
        student: 学生数据（含 tasks 得分）
        subtask_mapping: 子任务→知识点映射
        task_stats: 子任务统计（含 max_score）
    
    返回:
        {
            "name": 学生名,
            "student_id": 学号,
            "weak_knowledge": [知识点列表],
            "weak_subtasks": [薄弱子任务列表],
            "weakness_rate": 薄弱程度(0~1),
        }
    """
    weak_subtasks = []
    weak_knowledge_raw = []  # 知识点名称列表（可重复）
    seen_knowledge = set()
    weak_knowledge_detail = []  # 去重后的详细知识点

    student_tasks = student.get("tasks", {})

    for tid, mapping in sorted(
        subtask_mapping.items(),
        key=lambda x: int(x[0].replace("task", "")),
    ):
        task_info = task_stats.get(tid, {})
        max_score = task_info.get("max_score", 0)
        if max_score <= 0:
            continue

        # 学生在该子任务的得分
        student_task = student_tasks.get(tid, {})
        score = student_task.get("score", 0)
        if score is None:
            score = 0

        rate = score / max_score

        if rate < WEAK_THRESHOLD:
            # 这是一个薄弱子任务
            weak_subtasks.append({
                "task_id": tid,
                "name": mapping.get("name", tid),
                "score": score,
                "max_score": max_score,
                "rate": round(rate, 2),
            })

            # 收集关联的知识点
            for k in mapping.get("knowledge", []):
                name = k.get("视频/知识点名称", "")
                unit = k.get("MOOC教学单元", "")
                level = k.get("教学层次", "")
                key = f"{unit}|{name}"
                weak_knowledge_raw.append(name)
                if key not in seen_knowledge:
                    seen_knowledge.add(key)
                    weak_knowledge_detail.append({
                        "name": name,
                        "unit": unit,
                        "level": level,
                        "related_subtask": mapping.get("name", tid),
                    })

    # 按教学单元分组统计薄弱知识
    unit_weakness = defaultdict(list)
    for kw in weak_knowledge_detail:
        unit = kw["unit"] or "未分类"
        unit_weakness[unit].append(kw)

    # 统计该学生薄弱程度
    total_subtasks = len([t for t in task_stats.values() if t.get("max_score", 0) > 0])
    weakness_rate = len(weak_subtasks) / total_subtasks if total_subtasks > 0 else 0

    return {
        "name": student.get("name", ""),
        "student_id": student.get("student_id", ""),
        "final_score": student.get("final_score", 0),
        "weak_subtasks": weak_subtasks,
        "weak_knowledge": weak_knowledge_detail,
        "unit_weakness": dict(unit_weakness),
        "weak_subtask_count": len(weak_subtasks),
        "weak_knowledge_count": len(weak_knowledge_detail),
        "weakness_rate": round(weakness_rate, 2),
    }


# 需要排除的教师/管理员姓名（可在 web/config/settings.json 的 exclude_names 中调整）
EXCLUDED_NAMES = set(load_config()["exclude_names"])


def is_real_student(name: str) -> bool:
    """
    判断是否为真实学生姓名（过滤测试账号、教师、实验标题等）。
    """
    if not name or not name.strip():
        return False
    name = name.strip()
    if name in EXCLUDED_NAMES:
        return False
    # 如果姓名中不包含任何中文字符，视为非真实学生
    if not re.search(r'[\u4e00-\u9fff]', name):
        return False
    # 过滤疑似实验标题的长字符串
    if len(name) > 6:
        bad_keywords = ["实验", "练习", "项目", "测试", "作业", "202", "汇总", "分析"]
        if any(k in name for k in bad_keywords):
            return False
    return True


def analyze_all_students(
    experiment_result: Dict, knowledge_base: List[Dict]
) -> Dict:
    """
    分析一个实验中所有学生的薄弱知识点。
    
    返回:
        {
            "experiment_name": "...",
            "subtask_mapping": {...},
            "student_results": [每个学生的分析结果],
            "knowledge_error_rate": {"知识名称": 错误率, ...},
            "top_error_knowledge": [错误率最高的N个知识点],
        }
    """
    students = experiment_result.get("students", [])
    task_stats = experiment_result.get("task_stats", {})

    # 构建子任务→知识点映射
    subtask_mapping = build_experiment_subtask_mapping(
        experiment_result, knowledge_base
    )

    # 逐个学生分析
    student_results = []
    for s in students:
        # 兜底过滤非真实学生
        if not is_real_student(s.get("name", "")):
            continue
        result = analyze_student_weakness(s, subtask_mapping, task_stats)
        student_results.append(result)

    # 按薄弱程度排序（最薄弱在前，无薄弱在后）
    student_results.sort(key=lambda x: -x["weakness_rate"])

    # 有薄弱项的学生数
    weak_student_count = sum(
        1 for r in student_results if r["weak_knowledge_count"] > 0 or r["weak_subtask_count"] > 0
    )

    # 统计每个知识点被多少学生标记为薄弱 → 计算"易错率"
    total_students = len(students)
    knowledge_error_counter = Counter()
    knowledge_unit_map = {}  # 知识点名称 → 所属单元

    for sr in student_results:
        for kw in sr["weak_knowledge"]:
            name = kw["name"]
            knowledge_error_counter[name] += 1
            if name not in knowledge_unit_map:
                knowledge_unit_map[name] = {
                    "unit": kw["unit"],
                    "level": kw["level"],
                }

    knowledge_error_rate = {}
    for name, count in knowledge_error_counter.items():
        knowledge_error_rate[name] = {
            "error_count": count,
            "error_rate": round(count / total_students, 2) if total_students > 0 else 0,
            "unit": knowledge_unit_map.get(name, {}).get("unit", ""),
            "level": knowledge_unit_map.get(name, {}).get("level", ""),
        }

    # 按易错率排序
    top_error = sorted(
        knowledge_error_rate.items(),
        key=lambda x: -x[1]["error_rate"],
    )

    return {
        "experiment_name": experiment_result.get("experiment_name", ""),
        "student_count": total_students,
        "weak_student_count": weak_student_count,
        "subtask_mapping": subtask_mapping,
        "student_results": student_results,
        "knowledge_error_rate": knowledge_error_rate,
        "top_error_knowledge": top_error[:20],
    }


def generate_student_report(student_result: Dict, experiment_name: str) -> str:
    """生成单个学生的薄弱知识点报告"""
    lines = []
    lines.append("=" * 64)
    lines.append(f"  学生个人分析报告")
    lines.append("=" * 64)
    lines.append(f"实验：{experiment_name}")
    lines.append(f"姓名：{student_result['name']}")
    lines.append(f"学号：{student_result['student_id']}")
    lines.append(f"总分：{student_result.get('final_score', 'N/A')}")
    lines.append(f"薄弱子任务：{student_result['weak_subtask_count']}个"
                 f"（薄弱率 {student_result['weakness_rate']*100:.0f}%）")
    lines.append("")

    # 薄弱子任务详情
    if student_result["weak_subtasks"]:
        lines.append("-" * 64)
        lines.append("【薄弱子任务】")
        lines.append("-" * 64)
        for wt in student_result["weak_subtasks"]:
            lines.append(
                f"  {wt['name']:<30} "
                f"得分 {wt['score']:.0f}/{wt['max_score']:.0f} "
                f"({wt['rate']*100:.0f}%)"
            )
        lines.append("")

    # 薄弱知识点
    if student_result["weak_knowledge"]:
        lines.append("-" * 64)
        lines.append("【对应薄弱知识点】")
        lines.append("-" * 64)
        sorted_units = sorted(student_result["unit_weakness"].keys(), key=_unit_sort_key)
        for unit in sorted_units:
            kws = student_result["unit_weakness"][unit]
            lines.append(f"  [{unit}]")
            for kw in kws:
                lines.append(f"    · {kw['name']}（来自：{kw['related_subtask']}）")
        lines.append("")

    # 总结
    if student_result["weak_subtask_count"] > 0:
        lines.append("-" * 64)
        lines.append("【学习建议】")
        lines.append("-" * 64)
        units = sorted(student_result["unit_weakness"].keys(), key=_unit_sort_key)
        if units:
            lines.append(f"  需要加强的单元：{'、'.join(units)}")
        weak_pct = student_result["weakness_rate"] * 100
        if weak_pct >= 50:
            lines.append("  整体掌握情况较差，建议系统复习")
        elif weak_pct >= 30:
            lines.append("  部分知识点掌握不牢，建议针对性复习")
        else:
            lines.append("  个别知识点需要巩固")

    lines.append("")
    lines.append("=" * 64)
    lines.append(f"  报告生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 64)
    return "\n".join(lines)


def generate_class_summary(analysis_result: Dict) -> str:
    """生成班级汇总报告（含知识易错统计）"""
    lines = []
    lines.append("=" * 64)
    lines.append(f"  班级薄弱知识点汇总")
    lines.append("=" * 64)
    lines.append(f"实验：{analysis_result['experiment_name']}")
    lines.append(f"学生总数：{analysis_result['student_count']}")
    lines.append(f"有薄弱项的学生：{analysis_result.get('weak_student_count', len(analysis_result.get('student_results', [])))}人")
    lines.append("")

    # 易错知识点排行
    top_error = analysis_result.get("top_error_knowledge", [])
    if top_error:
        lines.append("-" * 64)
        lines.append("【知识点易错率排行】")
        lines.append("-" * 64)
        header = f"{'知识点名称':<28} {'所属单元':<20} {'易错人数':<10} {'易错率':<8}"
        lines.append(header)
        lines.append("-" * 64)
        for name, info in top_error:
            unit = info["unit"][:18] if info["unit"] else "-"
            lines.append(
                f"{name[:26]:<28} {unit:<20} "
                f"{info['error_count']:<10} {info['error_rate']*100:.0f}%"
            )
        lines.append("")

    # 所有学生薄弱概况
    lines.append("-" * 64)
    lines.append("【各学生薄弱概况】")
    lines.append("-" * 64)
    header = f"{'姓名':<12} {'学号':<16} {'薄弱子任务':<12} {'薄弱知识点':<12} {'薄弱率':<8}"
    lines.append(header)
    lines.append("-" * 64)
    for sr in analysis_result.get("student_results", []):
        lines.append(
            f"{sr['name']:<12} {sr.get('student_id', ''):<16} "
            f"{sr['weak_subtask_count']:<12} {sr['weak_knowledge_count']:<12} "
            f"{sr['weakness_rate']*100:.0f}%"
        )

    lines.append("")
    lines.append("=" * 64)
    lines.append(f"  报告生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 64)
    return "\n".join(lines)
