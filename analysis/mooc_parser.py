"""
MOOC（课堂成绩）数据解析器。

格式：每个 CSV 文件是一个班级的成绩单。
成绩构成（来自老师的成绩指标文档）：
  - testScore：          单元练习（50分）
  - assignmentScore：    互评作业（20分）
  - discussScore：       课程讨论（10分）
  - examScore：          综合测试（20分）
  - 以上四项合计 100 分，即线上成绩
  - practiceScoreAmount：课堂练习得分（满分未知，仅展示原始分）
  - videoViewCount：     观看视频个数
  - timeOfViewInMilli：  视频观看时长(ms)
  - signinCount：        签到次数
  - sigininAbsentCount： 缺勤次数
  - countReply：         论坛回复次数
  - practiceCorrectCount：课堂练习答对题数
  - level：              课程等级（合格/不合格）
"""

import csv
import codecs
import logging
from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Optional, Union

from analysis.config import load_config

logger = logging.getLogger(__name__)

# 薄弱判定阈值（可在 web/config/settings.json 中调整）
_WEAK_THRESHOLD = load_config()["weak_threshold"]

# 需要分析的数值列
NUMERIC_COLS = [
    "testScore", "examScore", "assignmentScore", "discussScore",
    "practiceScoreAmount", "practiceCorrectCount",
    "totalScore", "videoViewCount", "signinCount",
    "sigininAbsentCount", "signinLeaveCount", "countReply",
    "timeOfViewInMilli",
]

# 主要考核项（用于展示）
ASSESSMENT_COLS = [
    "testScore", "examScore", "assignmentScore",
    "discussScore", "practiceScoreAmount",
]

# 已知满分的考核项（线上成绩的四个组成部分）
KNOWN_FULL_SCORES = {
    "testScore": 50,
    "examScore": 20,
    "assignmentScore": 20,
    "discussScore": 10,
}

ASSESSMENT_CN = {
    "testScore": "单元练习",
    "examScore": "综合测试",
    "assignmentScore": "互评作业",
    "discussScore": "课程讨论",
    "practiceScoreAmount": "课堂练习",
}

# 附加统计项
EXTRA_COLS = {
    "videoViewCount": "观看视频数",
    "signinCount": "签到次数",
    "sigininAbsentCount": "缺勤次数",
    "countReply": "论坛回复次数",
    "practiceCorrectCount": "练习答对题数",
}


def parse_classroom_name(filename: str) -> str:
    return Path(filename).stem


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


def load_mooc_class(filepath: str) -> Dict:
    encoding = _detect_encoding(filepath)
    with open(filepath, encoding=encoding) as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    students = []
    for r in rows:
        name = (r.get("realName") or "").strip()
        if not is_real_student(name):
            continue

        student = {
            "student_number": r.get("studentNumber", ""),
            "name": name,
            "level": r.get("level", "").strip(),
            "classroom_name": r.get("classroomName", ""),
        }
        for col in NUMERIC_COLS:
            student[col] = _parse_score(r.get(col))
        students.append(student)

    classroom_name = ""
    for s in students:
        if s["classroom_name"]:
            classroom_name = s["classroom_name"]
            break
    if not classroom_name:
        classroom_name = parse_classroom_name(filepath)

    return {
        "file_path": filepath,
        "classroom_name": classroom_name,
        "student_count": len(students),
        "students": students,
    }


def analyze_mooc_class(class_data: Dict) -> Dict:
    """
    分析 MOOC 班级成绩。
    仅做原始统计，不做得分率计算（因各课程满分规则不同）。
    """
    students = class_data["students"]
    total = len(students)

    # 各考核项原始统计（空值的自动跳过）
    assessment_stats = {}
    for col in ASSESSMENT_COLS:
        scores = [s[col] for s in students if s[col] is not None]
        if not scores:
            continue
        entry = {
            "name": ASSESSMENT_CN.get(col, col),
            "avg_score": round(sum(scores) / len(scores), 2),
            "min_score": min(scores),
            "max_score": max(scores),
            "zero_count": sum(1 for s in scores if s == 0),
        }
        # 已知满分的项计算得分率
        if col in KNOWN_FULL_SCORES:
            full = KNOWN_FULL_SCORES[col]
            entry["full_score"] = full
            entry["avg_rate"] = round(entry["avg_score"] / full, 2)
        assessment_stats[col] = entry

    # 有考核数据的学生数
    valid_count = sum(
        1 for s in students
        if any(s[col] is not None for col in ASSESSMENT_COLS)
    )

    # 附加项统计
    extra_stats = {}
    for col, cn_name in EXTRA_COLS.items():
        values = [s[col] for s in students if s[col] is not None]
        if not values:
            continue
        extra_stats[col] = {
            "name": cn_name,
            "avg": round(sum(values) / len(values), 2),
            "min": min(values),
            "max": max(values),
        }

    # 课程等级分布 — 这是老师评定的及格/不及格标准
    level_dist = defaultdict(int)
    for s in students:
        if s["level"]:
            level_dist[s["level"]] += 1

    # 不及格学生名单（按 level == "不合格"）
    fail_students = [
        {"name": s["name"]}
        for s in students if s["level"] == "不合格"
    ]

    # 识别短板（得分率低于薄弱阈值的已知满分项）
    weak_assessments = {}
    for col, stats in assessment_stats.items():
        rate = stats.get("avg_rate")
        if rate is not None and rate < _WEAK_THRESHOLD:
            weak_assessments[col] = stats

    return {
        "classroom_name": class_data["classroom_name"],
        "student_count": total,
        "valid_count": valid_count,
        "assessment_stats": assessment_stats,
        "extra_stats": extra_stats,
        "level_distribution": dict(level_dist),
        "fail_count": len(fail_students),
        "fail_students": fail_students,
        "weak_assessments": weak_assessments,
    }


def scan_mooc_directory(root_dir: str) -> List[Dict]:
    results = []
    root = Path(root_dir)
    for csv_file in sorted(root.glob("**/*.csv")):
        if csv_file.name.startswith("~$"):
            continue
        results.append({
            "file_path": str(csv_file),
            "filename": csv_file.name,
        })
    return results


def _parse_score(v: Optional[str]) -> Optional[float]:
    """
    解析分数值。
    - "" / None / "--" → None（缺失）
    - "-1" → 0.0（未交作业/未参与，按0分计）
    - 其他 → float
    """
    if v is None or v == "" or v == "--":
        return None
    try:
        val = float(v)
        return 0.0 if val < 0 else val
    except (ValueError, TypeError):
        return None


def _detect_encoding(filepath: str) -> str:
    """用增量解码器判定文件编码，避免固定截断 4096 字节在多字节字符中间导致误判"""
    with open(filepath, "rb") as f:
        raw = f.read()
    if not raw:
        return "utf-8"
    for enc in ("utf-8", "gbk"):
        try:
            decoder = codecs.getincrementaldecoder(enc)()
            decoder.decode(raw)
            decoder.decode(b"", final=True)
            return enc
        except UnicodeDecodeError:
            continue
    # 编码无法识别时不再静默回退，给出明确提示（避免乱码难排查）
    logger.warning(
        "文件编码识别失败（既非 UTF-8 也非 GBK），将按 UTF-8 尝试读取，可能出现乱码: %s",
        filepath,
    )
    return "utf-8"
