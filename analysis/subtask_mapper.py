"""
子任务→知识点映射器。

将头歌实验中的每个子任务（subtask）名称映射到知识点库条目。
子任务命名规律："知识点：案例" 或 "技术点 - 类名"
"""

import re
from typing import List, Dict, Optional


def extract_subtask_topic(subtask_name: str) -> str:
    """
    从子任务名称中提取核心知识点主题。
    
    "数据类型与变量：20个苹果加40个梨" → "数据类型与变量"
    "类的定义和使用 - 银行账户Account类" → "类的定义和使用"
    "基本输入输出 - 与计算机交互" → "基本输入输出"
    """
    name = subtask_name.strip()
    
    # 模式1："知识点：案例"（中文冒号）
    m = re.split(r'[：:]', name)
    if m and m[0].strip():
        topic = m[0].strip()
        if len(topic) >= 2 and len(topic) <= 30:
            return topic
    
    # 模式2："技术点 - 类名" 
    m = re.split(r'\s*[-–—]\s*', name)
    if m and m[0].strip():
        topic = m[0].strip()
        if len(topic) >= 2 and len(topic) <= 30:
            return topic
    
    # 模式3：直接用完整名称
    return name


def map_subtask_to_knowledge(
    subtask_name: str, knowledge_base: List[Dict]
) -> List[Dict]:
    """
    将单个子任务名称映射到知识点库。
    
    "动物抽象类" → 抽象类知识
    "宠物接口" → 接口知识
    "数据类型与变量：20个苹果加40个梨" → 数据类型与变量
    """
    from analysis.knowledge_builder import search_knowledge
    
    topic = extract_subtask_topic(subtask_name)
    if not topic:
        return []
    
    # 策略1：直接用完整主题搜索
    results = search_knowledge(topic, knowledge_base)
    if results:
        return results[:3]
    
    # 策略2：拆词搜索（"数据类型与变量" → "数据类型" "变量"）
    if len(topic) > 4:
        for sub_word in _split_topic(topic):
            results = search_knowledge(sub_word, knowledge_base)
            if results:
                return results[:3]
    
    # 策略3：渐进式去前缀
    # "动物抽象类" → "抽象类" → 匹配
    # "宠物接口" → "接口" → 匹配
    # "声音接口 - Java8/9接口新变化" → 已处理
    for start in range(1, len(topic)):
        shorter = topic[start:]
        if len(shorter) >= 2:
            results = search_knowledge(shorter, knowledge_base)
            if results:
                return results[:3]
    
    # 策略4：取每个中文字符相邻二元组
    # "动物抽象类" → "动物" "物抽" "抽象" "象类" "类" → "抽象" 可能匹配
    if len(topic) >= 3:
        for i in range(len(topic) - 1):
            pair = topic[i:i+2]
            if len(pair) == 2:
                results = search_knowledge(pair, knowledge_base)
                if results:
                    return results[:3]
    
    return []


def _split_topic(topic: str) -> List[str]:
    """将复合主题拆分成更小的关键词"""
    # 常见分隔词
    words = re.split(r'[与和及、,，]', topic)
    return [w.strip() for w in words if len(w.strip()) >= 2]


def build_subtask_mapping(
    task_definitions: List[Dict], knowledge_base: List[Dict]
) -> Dict[str, List[Dict]]:
    """
    为整个实验的所有子任务构建知识点映射。
    
    返回: {"task1": [...知识点...], "task2": [...], ...}
    结果会被缓存，避免重复搜索。
    """
    mapping = {}
    for td in task_definitions:
        tid = f"task{td['task_id']}"
        # 子任务名称可能从学生数据中获取
        # 这里先用占位，实际名称在 analyze 时传入
        mapping[tid] = []
    return mapping


def get_subtask_name_from_students(
    students: List[Dict], tid: str
) -> str:
    """从学生数据中获取子任务名称（取第一个非空值）"""
    for s in students:
        task = s.get("tasks", {}).get(tid, {})
        name = task.get("name", "")
        if name:
            return name
    return ""


def build_experiment_subtask_mapping(
    experiment_result: Dict, knowledge_base: List[Dict]
) -> Dict[str, dict]:
    """
    为一个实验构建完整的子任务→知识点映射。
    
    返回: {
        "task1": {"name": "子任务名", "knowledge": [...知识点...]},
        "task2": {"name": "...", "knowledge": [...]},
        ...
    }
    """
    students = experiment_result.get("students", [])
    task_stats = experiment_result.get("task_stats", {})
    
    mapping = {}
    for tid in sorted(task_stats.keys(), key=lambda x: int(x.replace("task", ""))):
        name = get_subtask_name_from_students(students, tid)
        if not name:
            # 回退到 task_stats 中的 task_name
            name = task_stats[tid].get("task_name", "")
        
        knowledge = map_subtask_to_knowledge(name, knowledge_base) if name else []
        mapping[tid] = {
            "name": name,
            "knowledge": knowledge,
        }
    
    return mapping
