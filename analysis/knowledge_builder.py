"""
知识点库构建器。

将"智慧慕课-慕课章节对应表.xlsx" 转换为结构化知识点库 CSV。
"""

import csv
import re
import unicodedata
from pathlib import Path
from typing import List, Dict, Optional


# 教学层次到知识领域的映射
LEVEL_MAP = {
    "基础语法": "Java基础语法",
    "面向对象": "Java面向对象",
    "高级程序设计": "Java高级编程",
}


def build_knowledge_from_chapter_map(
    excel_path: str, output_path: str
) -> List[Dict]:
    """
    从慕课章节对应表 Excel 生成知识点库 CSV。

    提取逻辑：
    - 合并同序号的行（一个序号一行，含教学层次、MOOC单元、项目名称、视频内容）
    - 每个知识点条目包含：序号、教学层次、MOOC单元、项目名称、视频内容
    """
    import openpyxl

    wb = openpyxl.load_workbook(excel_path, data_only=True)
    ws = wb.active

    records = []
    current_level = ""
    current_unit = ""
    current_project = ""
    seq = 0

    for row in ws.iter_rows(min_row=3, values_only=True):
        # 列: A=None, B=序号, C=教学层次, D=MOOC教学单元, E=项目名称, F=视频内容, G=时长
        b, c, d, e, f, g = row[1], row[2], row[3], row[4], row[5], row[6]

        # 检查是否有新序号
        if b is not None:
            try:
                seq = int(b)
            except (ValueError, TypeError):
                continue

        # 更新当前教学层次
        if c and str(c).strip():
            current_level = str(c).strip()

        # 更新当前 MOOC 单元
        if d and str(d).strip():
            current_unit = str(d).strip()

        # 更新当前项目名称
        if e and str(e).strip():
            current_project = str(e).strip()

        knowledge_area = LEVEL_MAP.get(current_level, current_level)

        video_name = str(f).strip() if f else ""
        duration = str(g).strip() if g else ""

        if video_name or seq > 0:
            records.append({
                "知识编号": seq,
                "教学层次": current_level,
                "知识领域": knowledge_area,
                "MOOC教学单元": current_unit,
                "项目名称": current_project,
                "视频/知识点名称": video_name,
                "视频时长": duration,
            })

    # 去重：相同序号+相同视频内容的只保留一条
    seen = set()
    unique_records = []
    for r in records:
        key = (r["知识编号"], r["视频/知识点名称"])
        if key not in seen:
            seen.add(key)
            unique_records.append(r)

    # 写出 CSV
    fieldnames = [
        "知识编号", "教学层次", "知识领域", "MOOC教学单元",
        "项目名称", "视频/知识点名称", "视频时长",
    ]
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(unique_records)

    return unique_records


def _normalize_knowledge_name(name: str) -> str:
    """
    归一化知识点名称，用于去重判断。
    
    处理：
    - NFKC 归一化（康熙部首→标准汉字，如 ⽅→方）
    - 去掉开头的编号前缀（如 "8.2 "、"4.4 "、"1. "）
    - 去掉所有空格（防止 "Static关键字" vs "Static 关键字" 等差异）
    - 转为小写
    """
    name = unicodedata.normalize('NFKC', name.strip())
    # 去掉 "X.X " 或 "X.XX " 或 "X " 等编号前缀
    name = re.sub(r'^\d+(\.\d+)*\s*', '', name)
    # 去掉所有空格（防止 "Static 关键字" 和 "Static关键字" 被视为不同）
    name = re.sub(r'\s+', '', name)
    return name.lower()


def load_knowledge_base(csv_path: str) -> List[Dict]:
    """
    加载已生成的知识点库 CSV，并做去重处理。
    
    去重规则：按归一化后的"视频/知识点名称"去重，保留首次出现的条目。
    MOOC 章节表条目（带编号）在前，PDF 大纲条目在后，
    保留 MOOC 条目的更完整元数据。
    """
    records = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(row)

    # 按归一化名称去重
    seen = set()
    deduped = []
    for r in records:
        raw_name = r.get("视频/知识点名称", "").strip()
        if not raw_name:
            # 没有名称的条目（如仅有序号无内容的行）直接保留
            deduped.append(r)
            continue
        key = _normalize_knowledge_name(raw_name)
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    return deduped


# 向量化检索缓存（知识库内容不变时复用索引）
_index_cache_key: Optional[tuple] = None
_index_cache: Optional[Dict] = None

# 语义搜索分数阈值：字符 n-gram 余弦，相关文本一般 >= 0.15
SEMANTIC_THRESHOLD = 0.15


def _get_knowledge_index(knowledge_base: List[Dict]) -> Dict:
    """按知识库内容构建（带缓存）向量索引"""
    global _index_cache_key, _index_cache
    from analysis.vector_search import build_knowledge_index

    key = tuple(
        sorted(_normalize_knowledge_name(r.get("视频/知识点名称", "")) for r in knowledge_base)
    )
    if key != _index_cache_key or _index_cache is None:
        _index_cache = build_knowledge_index(knowledge_base)
        _index_cache_key = key
    return _index_cache


def search_knowledge(
    keyword: str, knowledge_base: List[Dict]
) -> List[Dict]:
    """
    在知识点库中检索相关条目（向量化语义匹配，关键字子串匹配兜底）。

    语义匹配可处理近似表述与顺序差异：
    - "动物抽象类" → "5.4 抽象类与接口"
    - "Java注释符号" → "注释"相关条目
    分数低于阈值时回退到原来的子串匹配，保证召回不倒退。
    """
    from analysis.vector_search import search_semantic

    if not keyword or not knowledge_base:
        return []

    index = _get_knowledge_index(knowledge_base)
    scored = search_semantic(keyword, index, top_k=10)
    results = [entry for score, entry in scored if score >= SEMANTIC_THRESHOLD]
    if results:
        return results

    # 兜底：关键字子串匹配（原逻辑）
    results = []
    low = keyword.lower()
    for entry in knowledge_base:
        name = entry.get("视频/知识点名称", "")
        unit = entry.get("MOOC教学单元", "")
        project = entry.get("项目名称", "")
        if low in name.lower() or low in unit.lower() or low in project.lower():
            results.append(entry)
    return results


def map_experiment_to_knowledge(
    experiment_name: str, knowledge_base: List[Dict]
) -> List[Dict]:
    """
    将头歌实验名称映射到知识点。
    通过实验名称中的关键词匹配知识库中的视频/知识点名称和项目名称。
    """
    # 从实验名称提取关键词（去掉"Java"等通用词）
    keywords = (experiment_name
                .replace("Java", "")
                .replace("java", "")
                .replace("之", "")
                .replace("与", "")
                .replace("-", " ")
                .strip())
    results = search_knowledge(keywords, knowledge_base)

    # 如果没有匹配到，尝试用教学层次匹配
    if not results:
        for level_name in ["基础语法", "面向对象", "高级程序设计"]:
            if level_name in experiment_name:
                for entry in knowledge_base:
                    if entry.get("教学层次") == level_name:
                        results.append(entry)
                    if len(results) >= 5:  # 最多取5条
                        break
                break

    return results
