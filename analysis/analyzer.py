"""
统一分析引擎。

根据数据源类型（头歌/MOOC），选择合适的解析和策略进行分析。
"""

from typing import Dict, List, Optional
from analysis.touge_parser import (
    load_touge_experiment,
    analyze_experiment,
    scan_touge_directory,
)
from analysis.mooc_parser import (
    load_mooc_class,
    analyze_mooc_class,
    scan_mooc_directory,
)
from analysis.knowledge_builder import (
    load_knowledge_base,
    map_experiment_to_knowledge,
    search_knowledge,
)


def analyze_touge_file(filepath: str, knowledge_base: Optional[List[Dict]] = None) -> Dict:
    """分析单个头歌实验文件"""
    exp = load_touge_experiment(filepath)
    result = analyze_experiment(exp)

    # 携带原始学生数据，用于后续学生级分析
    result["students"] = exp.get("students", [])
    result["task_definitions"] = exp.get("task_definitions", [])

    # 如果提供了知识库，尝试映射
    if knowledge_base and result.get("experiment_name"):
        knowledge = map_experiment_to_knowledge(
            result["experiment_name"], knowledge_base
        )
        result["knowledge_mapping"] = knowledge

    result["source_type"] = "头歌"
    result["file_path"] = filepath
    return result


def analyze_touge_directory(
    root_dir: str, knowledge_base: Optional[List[Dict]] = None
) -> List[Dict]:
    """分析头歌目录下的所有实验"""
    files = scan_touge_directory(root_dir)
    results = []
    for f in files:
        try:
            result = analyze_touge_file(f["file_path"], knowledge_base)
            result["course"] = f["course"]
            results.append(result)
        except Exception as e:
            results.append({
                "source_type": "头歌",
                "file_path": f["file_path"],
                "error": str(e),
            })
    return results


def analyze_mooc_file(filepath: str, knowledge_base: Optional[List[Dict]] = None) -> Dict:
    """分析单个 MOOC 班级文件"""
    cls = load_mooc_class(filepath)
    result = analyze_mooc_class(cls)

    # 如果提供了知识库，搜索相关知识点
    if knowledge_base:
        related_knowledge = search_knowledge(
            result.get("classroom_name", ""), knowledge_base
        )
        result["knowledge_mapping"] = related_knowledge[:10]  # 只取前10条

    result["source_type"] = "MOOC"
    result["file_path"] = filepath
    return result


def analyze_mooc_directory(
    root_dir: str, knowledge_base: Optional[List[Dict]] = None
) -> List[Dict]:
    """分析 MOOC 目录下的所有班级"""
    files = scan_mooc_directory(root_dir)
    results = []
    for f in files:
        try:
            result = analyze_mooc_file(f["file_path"], knowledge_base)
            results.append(result)
        except Exception as e:
            results.append({
                "source_type": "MOOC",
                "file_path": f["file_path"],
                "error": str(e),
            })
    return results


def detect_data_source(filepath: str) -> str:
    """自动检测数据源类型"""
    filepath_lower = filepath.lower()
    if "touge" in filepath_lower or "头歌" in filepath_lower:
        return "touge"
    if "mooc" in filepath_lower or "class" in filepath_lower:
        return "mooc"
    # 通过文件名格式判断
    import re
    name = filepath.split("\\")[-1].split("/")[-1]
    if re.match(r"\d+_", name):
        return "touge"
    if name.lower().startswith("class"):
        return "mooc"
    # 文件名不可靠（如上传后 safe_name 编码丢失原名特征）时，读表头做内容级判断
    return _detect_by_header(filepath)


def _detect_by_header(filepath: str) -> str:
    """内容级兜底：读 CSV 前几行表头，按列名特征判断头歌 / MOOC。

    头歌实验：user_name, student_id, group_name, final_score, task1_name ...
    MOOC 成绩：userName/testScore/assignmentScore/examScore/videoViewCount ...
    已知限制：表头也被混淆时返回 unknown，由上层按失败处理，不强行猜测。
    """
    import re
    try:
        with open(filepath, "r", encoding="utf-8-sig", errors="ignore") as f:
            head = " ".join(f.readline() for _ in range(5))
    except OSError:
        return "unknown"
    head = head.lower()
    if re.search(r"final_score|sum_evaluate_count|task\d+_name|evaluate_count", head):
        return "touge"
    if re.search(r"testscore|assignmentscore|discussscore|examscore|videoviewcount|signincount", head):
        return "mooc"
    return "unknown"
