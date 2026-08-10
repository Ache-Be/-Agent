"""
报告生成器。

根据分析结果生成可读的文本报告。
"""

from typing import Dict, List, Optional
from datetime import datetime

from analysis.config import load_config

_WEAK_THRESHOLD = load_config()["weak_threshold"]


def generate_touge_report(result: Dict) -> str:
    """生成单个头歌实验的分析报告"""
    lines = []
    lines.append("=" * 64)
    lines.append(f"  实验分析：{result.get('experiment_name', '未知')}")
    lines.append("=" * 64)
    lines.append(f"学生人数：{result.get('student_count', 0)}")
    lines.append(f"平均分  ：{result.get('avg_final_score', 0)}")

    # 得分分布
    lines.append(f"最低分  ：{result.get('min_final_score', 0)}")
    lines.append(f"最高分  ：{result.get('max_final_score', 0)}")
    lines.append(f"低分人数：{result.get('low_score_count', 0)} "
                 f"({result.get('low_score_rate', 0)*100:.0f}%)")
    lines.append("")

    # 子任务详情
    task_stats = result.get("task_stats", {})
    if task_stats:
        lines.append("-" * 64)
        lines.append("【各子任务得分详情】")
        lines.append("-" * 64)
        for tid in sorted(task_stats.keys(), key=lambda x: int(x.replace("task", ""))):
            ts = task_stats[tid]
            lines.append(f"\n  {ts['task_name']}")
            avg = ts.get("avg_score", 0)
            max_s = ts.get("max_score", 0)
            rate = (avg / max_s * 100) if max_s > 0 else 0
            lines.append(f"    平均分：{avg}/{max_s} ({rate:.0f}%)")
            lines.append(f"    满分率：{ts.get('full_score_rate', 0)*100:.0f}%")
            lines.append(f"    查答案率：{ts.get('view_answer_rate', 0)*100:.0f}%")

    # 薄弱子任务
    weak_tasks = result.get("weak_tasks", {})
    if weak_tasks:
        lines.append("")
        lines.append("-" * 64)
        lines.append(f"【需关注的薄弱子任务（平均得分率 < {_WEAK_THRESHOLD*100:.0f}%）】")
        lines.append("-" * 64)
        for tid in sorted(weak_tasks.keys(), key=lambda x: int(x.replace("task", ""))):
            wt = weak_tasks[tid]
            avg = wt.get("avg_score", 0)
            max_s = wt.get("max_score", 0)
            rate = (avg / max_s * 100) if max_s > 0 else 0
            lines.append(f"  ◆ {wt['task_name']}（得分率 {rate:.0f}%）")
            if wt.get("view_answer_rate", 0) > 0.3:
                lines.append(f"    [注意] 查看答案比例较高 ({wt['view_answer_rate']*100:.0f}%)")

    # 知识点映射
    knowledge = result.get("knowledge_mapping", [])
    if knowledge:
        lines.append("")
        lines.append("-" * 64)
        lines.append("【关联知识点】")
        lines.append("-" * 64)
        seen_units = set()
        for k in knowledge[:8]:
            unit = k.get("MOOC教学单元", "")
            if unit and unit not in seen_units:
                seen_units.add(unit)
                lines.append(f"  · {unit}")
            name = k.get("视频/知识点名称", "")
            if name:
                lines.append(f"    - {name}")

    lines.append("")
    return "\n".join(lines)


def generate_mooc_report(result: Dict) -> str:
    """生成单个 MOOC 班级的分析报告"""
    lines = []
    lines.append("=" * 64)
    lines.append(f"  班级分析：{result.get('classroom_name', '未知')}")
    lines.append("=" * 64)
    lines.append(f"学生总人数：{result.get('student_count', 0)}"
                 f"（有考核数据：{result.get('valid_count', 0)}）")

    # 课程及格概况（按 level 字段）
    level_dist = result.get("level_distribution", {})
    pass_count = level_dist.get("合格", 0)
    fail_count = result.get("fail_count", 0)
    leveled_count = pass_count + fail_count
    no_level_count = result.get("valid_count", 0) - leveled_count
    if leveled_count > 0:
        lines.append(f"及格人数：{pass_count}人 ({pass_count/leveled_count*100:.0f}%)")
        lines.append(f"不及格人数：{fail_count}人 ({fail_count/leveled_count*100:.0f}%)")
    if no_level_count > 0:
        lines.append(f"未评定等级：{no_level_count}人（数据中无 level 字段）")
    lines.append("")

    # 各考核项
    assessment_stats = result.get("assessment_stats", {})
    if assessment_stats:
        lines.append("-" * 64)
        lines.append("【各考核项成绩】")
        lines.append("-" * 64)
        # 按得分率从低到高排序（有得分率的在前），其余按原始顺序
        ordered = sorted(
            assessment_stats.items(),
            key=lambda x: x[1].get("avg_rate", 1),
        )
        for col, stats in ordered:
            full = stats.get("full_score")
            rate = stats.get("avg_rate")
            if full and rate is not None:
                marker = " <<< 短板" if rate < _WEAK_THRESHOLD else ""
                lines.append(
                    f"  {stats['name']:<12} "
                    f"均分 {stats['avg_score']:<8} / {full:<4} "
                    f"({rate*100:.0f}%){marker}"
                )
            else:
                # 课堂练习等无满分项
                lines.append(
                    f"  {stats['name']:<12} "
                    f"均分 {stats['avg_score']:<8} "
                    f"(范围 {stats['min_score']}~{stats['max_score']})"
                )

    # 附加统计项
    extra_stats = result.get("extra_stats", {})
    if extra_stats:
        lines.append("")
        lines.append("-" * 64)
        lines.append("【附加统计】")
        lines.append("-" * 64)
        header2 = f"{'项目':<16} {'班级均分':<12} {'最低':<12} {'最高':<12}"
        lines.append(header2)
        lines.append("-" * 64)
        for col, stats in extra_stats.items():
            lines.append(
                f"{stats['name']:<16} {stats['avg']:<12} "
                f"{stats['min']:<12} {stats['max']:<12}"
            )

    # 等级分布
    if level_dist:
        lines.append("")
        lines.append("-" * 64)
        lines.append("【课程等级分布（由教师评定）】")
        lines.append("-" * 64)
        for level, count in sorted(level_dist.items(), key=lambda x: -x[1]):
            if level:
                lines.append(f"  {level}：{count}人")
        lines.append("  （注：等级由课程平台根据教师设定的规则自动评定）")

    # 短板总结
    weak = result.get("weak_assessments", {})
    if weak:
        weak_names = [s["name"] for s in weak.values()]
        lines.append("")
        lines.append("-" * 64)
        lines.append("【需关注短板】")
        lines.append("-" * 64)
        lines.append(f"  {'、'.join(weak_names)} 得分率偏低（< {_WEAK_THRESHOLD*100:.0f}%）")
        lines.append("  建议查看具体题目，分析原因")
        lines.append("  （后续可上传题目原文进行知识点匹配）")

    # 不及格学生
    fail_list = result.get("fail_students", [])
    if fail_list:
        lines.append("")
        lines.append("-" * 64)
        lines.append(f"【不及格学生（共{len(fail_list)}人）】")
        lines.append("-" * 64)
        for s in fail_list[:15]:
            lines.append(f"  {s.get('name', '')}")
        if len(fail_list) > 15:
            lines.append(f"  ... 还有 {len(fail_list) - 15} 人")

    # 知识点映射
    knowledge = result.get("knowledge_mapping", [])
    if knowledge:
        lines.append("")
        lines.append("-" * 64)
        lines.append("【关联课程知识点】")
        lines.append("-" * 64)
        for k in knowledge[:5]:
            unit = k.get("MOOC教学单元", "")
            name = k.get("视频/知识点名称", "")
            if unit and name:
                lines.append(f"  · {unit} → {name}")

    lines.append("")
    return "\n".join(lines)


def generate_summary_report(
    touge_results: List[Dict], mooc_results: List[Dict]
) -> str:
    """生成汇总报告"""
    lines = []
    lines.append("=" * 64)
    lines.append("  教学预警汇总报告")
    lines.append("=" * 64)
    lines.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # ---- 头歌汇总 ----
    touge_valid = [r for r in touge_results if "error" not in r]
    if touge_valid:
        lines.append("-" * 64)
        lines.append("【头歌实验预警】")
        lines.append("-" * 64)
        # 按低分率排序
        touge_sorted = sorted(
            touge_valid,
            key=lambda x: x.get("low_score_rate", 0),
            reverse=True,
        )
        header = f"{'实验名称':<30} {'人数':<6} {'均分':<8} {'低分率':<8} {'薄弱子任务':<10}"
        lines.append(header)
        lines.append("-" * 64)
        for r in touge_sorted[:20]:
            name = r.get("experiment_name", "")[:28]
            weak_count = len(r.get("weak_tasks", {}))
            lines.append(
                f"{name:<30} {r.get('student_count', 0):<6} "
                f"{r.get('avg_final_score', 0):<8} "
                f"{r.get('low_score_rate', 0)*100:.0f}%{'':<4} "
                f"{weak_count}个"
            )

    # ---- MOOC 汇总 ----
    mooc_valid = [r for r in mooc_results if "error" not in r]
    if mooc_valid:
        lines.append("")
        lines.append("-" * 64)
        lines.append("【MOOC班级预警】")
        lines.append("-" * 64)
        for r in mooc_valid:
            weak = r.get("weak_assessments", {})
            name = r.get("classroom_name", "")[:30]
            if weak:
                weak_names = [s["name"] for s in weak.values()]
                lines.append(f"  ◆ {name} — {'/'.join(weak_names)} 偏低")
            fn = r.get("fail_count", 0)
            vn = r.get("valid_count", 1)
            if fn > 0:
                lines.append(f"    [警告] 不及格 {fn}/{vn}人 ({fn/vn*100:.0f}%)")

    lines.append("")
    lines.append("=" * 64)
    lines.append("  报告结束")
    lines.append("=" * 64)
    return "\n".join(lines)
