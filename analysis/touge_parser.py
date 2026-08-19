"""
头歌（实验平台）数据解析器。

文件名格式：{ID}_{实验名称}.csv
数据格式：每行一个学生，包含 final_score 及 task1~taskN 的细分得分。
"""

import csv
import re
from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Tuple, Optional

from analysis.config import load_config

# 分析阈值（可在 web/config/settings.json 中调整）
_WEAK_THRESHOLD = load_config()["weak_threshold"]
_LOW_SCORE_LINE = load_config()["low_score_line"]


# 标准头歌 CSV 列结构（2022~2025 基本一致）
BASE_COLS = [
    "user_name", "student_id", "group_name", "cost_time",
    "sum_evaluate_count", "final_score", "sort", "efficiency", "capability",
]
TASK_PREFIX = "task"  # task1_name, task1_game_score, ...


def parse_filename(filepath: str) -> Dict:
    """从头歌 CSV 文件名中解析出实验 ID 和实验名称。
    兼容两种文件名格式：
      1. 原始 CSV 文件名：<数字ID>_<实验名称>.csv（match 从开头匹配）
      2. safe_name 编码后：<路径前缀__...___<数字ID>_<实验名称>.csv（路径前缀被替换成了双下划线 + 中文替换成下划线）
         这时从末尾反向匹配，避免路径前缀干扰数字正则。
    """
    name = Path(filepath).stem
    # 模式 1：原始格式（以数字开头，5 位以上）
    m = re.match(r"(\d{5,})_(.+)", name)
    if m:
        return {"experiment_id": m.group(1), "experiment_name": m.group(2)}
    # 模式 2：safe_name 末尾格式 —— __ 或 ___ 之后紧跟 5 位以上数字 _ 名称结束
    m = re.search(r"_{2,3}(\d{5,})_(.+)$", name)
    if m:
        return {"experiment_id": m.group(1), "experiment_name": m.group(2)}
    return {"experiment_id": "", "experiment_name": name}


def parse_tasks(headers: List[str]) -> List[Dict]:
    """
    从 CSV 表头中提取所有任务(task)的元信息。
    返回 [{"name_col": "task1_name", "score_col": "task1_game_score", ...}, ...]
    """
    tasks = []
    task_set = set()
    for h in headers:
        m = re.match(r"task(\d+)_(.+)", h)
        if m:
            task_set.add(int(m.group(1)))
    for tid in sorted(task_set):
        tasks.append({
            "task_id": tid,
            "name_col": f"task{tid}_name",
            "score_col": f"task{tid}_game_score",
            "time_col": f"task{tid}_time_consuming",
            "eval_col": f"task{tid}_evaluate_count",
            "view_col": f"task{tid}_view_answer",
        })
    return tasks


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


def load_touge_experiment(filepath: str) -> Dict:
    """
    加载单个头歌实验 CSV 文件。
    返回结构化的实验数据。
    """
    with open(filepath, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = list(reader)

    tasks = parse_tasks(headers)
    file_info = parse_filename(filepath)

    students = []
    for r in rows:
        user_name = (r.get("user_name") or "").strip()
        if not is_real_student(user_name):
            continue

        student = {
            "name": user_name,
            "student_id": r.get("student_id", ""),
            "group": r.get("group_name", ""),
            "final_score": _safe_float(r.get("final_score")),
            "cost_time": r.get("cost_time", ""),
            "evaluate_count": _safe_int(r.get("sum_evaluate_count")),
            "efficiency": _safe_float(r.get("efficiency")),
            "capability": _safe_float(r.get("capability")),
        }
        # 各子任务得分
        task_scores = {}
        for t in tasks:
            score = _safe_float(r.get(t["score_col"]))
            task_scores[f"task{t['task_id']}"] = {
                "name": r.get(t["name_col"], ""),
                "score": score if score is not None else 0,
                "time": r.get(t["time_col"], ""),
                "evaluate_count": _safe_int(r.get(t["eval_col"])),
                "view_answer": r.get(t["view_col"], ""),
            }
        student["tasks"] = task_scores
        students.append(student)

    return {
        "file_path": filepath,
        "experiment_id": file_info["experiment_id"],
        "experiment_name": file_info["experiment_name"],
        "task_count": len(tasks),
        "task_definitions": tasks,
        "student_count": len(students),
        "students": students,
    }


def analyze_experiment(experiment: Dict) -> Dict:
    """
    分析单个实验：统计各项指标。
    """
    students = experiment["students"]
    task_defs = experiment["task_definitions"]
    total = len(students)

    # 总体得分分布
    scores = [s["final_score"] for s in students if s["final_score"] is not None]

    # 各子任务统计
    task_stats = {}
    for t in task_defs:
        tid = f"task{t['task_id']}"
        all_scores = [s["tasks"][tid]["score"] for s in students
                      if s["tasks"][tid]["score"] is not None]

        # 子任务名称取第一个非空的
        task_name = ""
        for s in students:
            if s["tasks"][tid]["name"]:
                task_name = s["tasks"][tid]["name"]
                break

        # 多少人查看了答案
        view_answer_count = sum(
            1 for s in students if s["tasks"][tid]["view_answer"].lower() == "true"
        )

        task_stats[tid] = {
            "task_name": task_name,
            "max_score": max(all_scores) if all_scores else 0,
            "min_score": min(all_scores) if all_scores else 0,
            "avg_score": round(sum(all_scores) / len(all_scores), 2) if all_scores else 0,
            "pass_rate": round(sum(1 for s in all_scores if s > 0) / len(all_scores), 2)
            if all_scores else 0,
            "full_score_rate": round(sum(1 for s in all_scores if s == max(all_scores)) / len(all_scores), 2)
            if all_scores else 0,
            "view_answer_rate": round(view_answer_count / total, 2) if total else 0,
        }

    # 低分学生（final_score < 低分线）
    low_score_students = [s for s in students if s["final_score"] is not None and s["final_score"] < _LOW_SCORE_LINE]

    # 得分率低的子任务（平均分 < 满分的 薄弱阈值）
    weak_tasks = {}
    for tid, ts in task_stats.items():
        if ts["max_score"] > 0 and ts["avg_score"] / ts["max_score"] < _WEAK_THRESHOLD:
            weak_tasks[tid] = ts

    return {
        "experiment_name": experiment["experiment_name"],
        "student_count": total,
        "avg_final_score": round(sum(scores) / len(scores), 2) if scores else 0,
        "min_final_score": min(scores) if scores else 0,
        "max_final_score": max(scores) if scores else 0,
        "low_score_count": len(low_score_students),
        "low_score_rate": round(len(low_score_students) / total, 2) if total else 0,
        "weak_tasks": weak_tasks,
        "task_stats": task_stats,
    }


def scan_touge_directory(root_dir: str) -> List[Dict]:
    """
    扫描头歌数据目录，按课程分组返回所有实验文件。
    递归查找所有 *.csv，课程名取 CSV 所在目录名。
    适配不同深度的目录结构（如 头歌/tougeall/课程/ 或
    头歌/tougeall/学生数据/实验数据/课程/）。
    """
    results = []
    root = Path(root_dir)
    if not root.exists():
        return results
    for csv_file in sorted(root.rglob("*.csv")):
        results.append({
            "course": csv_file.parent.name,
            "file_path": str(csv_file),
            "filename": csv_file.name,
        })
    return results


def _safe_float(v) -> Optional[float]:
    if v is None or v == "" or v == "--":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _safe_int(v) -> Optional[int]:
    if v is None or v == "" or v == "--":
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None
