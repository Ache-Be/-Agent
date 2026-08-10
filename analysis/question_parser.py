"""
题目解析器：从 Word 文档中提取结构化题目，
并匹配知识点库。
"""

import re
from typing import List, Dict


def parse_word_questions(filepath: str) -> List[Dict]:
    """
    解析 Word 文档，提取所有题目。

    支持格式 1（顺序段落式）：选择题/判断题连续排列，无题号，答案内嵌
    支持格式 2（编号式）：n. 题目... 格式
    """
    from docx import Document

    doc = Document(filepath)
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]

    # 先尝试顺序段落式解析（综合测试格式）
    questions = _extract_sequential_questions(paragraphs)
    if questions:
        return questions

    # 再尝试编号式解析（阶段练习格式）
    questions = _extract_numbered_questions(paragraphs)
    if questions:
        return questions

    # 最后尝试纯判断题解析（课堂练习格式）
    questions = _extract_judgment_questions(paragraphs)
    return questions


def _extract_sequential_questions(paras: List[str]) -> List[Dict]:
    """
    解析顺序排列的题目（综合测试格式）。
    特点：无题号，[单选题]/[判断题] 标题标记类型，答案内嵌在题目中。
    """
    questions = []
    idx = 0
    q_num = 0
    i = 0
    while i < len(paras):
        p = paras[i]

        # 检测题型标题
        q_type = None
        if p == "[单选题]":
            q_type = "choice"
            i += 1
            continue
        elif p == "[判断题]":
            # 判断题：后续直接是题目，答案内嵌（对/错）
            j = i + 1
            while j < len(paras):
                if paras[j] in ("[单选题]", "[判断题]"):
                    break
                text = paras[j]
                answer_m = re.search(r'[（(]\s*([对错])\s*[）)]', text)
                if answer_m:
                    q_num += 1
                    questions.append({
                        "number": q_num,
                        "type": "judgment",
                        "question": re.sub(r'\s*[（(]\s*[对错]\s*[）)]\s*$', '', text),
                        "answer": answer_m.group(1),
                        "raw": text,
                    })
                j += 1
            i = j
            continue

        # 选择题：匹配形如 "问题内容（A）" 或 "问题内容（ A ）" 的题目
        # 注意：不能以 [ 开头，排除标题行
        if not p.startswith("[") and not p.startswith("（") and not p.startswith("("):
            choice_m = re.match(
                r'^(.*?)\s*[（(]\s*([A-Da-d])\s*[）)]\s*[。.]?\s*$', p
            )
            if choice_m:
                # 可能有前序代码（多选题跨行），检查
                q_text = choice_m.group(1)
                answer = choice_m.group(2).upper()

                # 如果上一行不是标题行且不以选项开头，拼接为题目的一部分
                if (i > 0
                    and paras[i-1] not in ("[单选题]", "[判断题]")
                    and not re.match(r'^[A-Da-d][.、]', paras[i-1])):
                    q_text = paras[i-1] + " " + q_text

                # 收集选项：接下来的行匹配 A. B. C. D.
                options = {}
                j = i + 1
                while j < len(paras):
                    opt_match = re.match(r'^([A-Da-d])[.、]\s*(.*)', paras[j])
                    if opt_match:
                        opt_letter = opt_match.group(1).upper()
                        opt_content = opt_match.group(2)
                        options[opt_letter] = opt_content
                        j += 1
                    else:
                        break

                if options:  # 确保真的有选项
                    q_num += 1
                    questions.append({
                        "number": q_num,
                        "type": "choice",
                        "question": q_text,
                        "options": options,
                        "answer": answer,
                        "raw": p,
                    })
                    i = j
                    continue
        i += 1

    return questions


def _extract_numbered_questions(paras: List[str]) -> List[Dict]:
    """
    解析带编号的题目（阶段练习/课后习题格式）。
    匹配 "n. 题目（A）" 或 "n. 题目" 格式。
    """
    questions = []
    i = 0
    while i < len(paras):
        p = paras[i]

        # 匹配 "n. 题目（A）" 或 "n、题目（A）"
        m = re.match(r'^(\d+)[.、]\s*(.*?)\s*[（(]\s*([A-Da-d])\s*[）)]', p)
        if m:
            q_num = int(m.group(1))
            q_text = m.group(2)
            answer = m.group(3).upper()

            # 收集选项
            options = {}
            j = i + 1
            while j < len(paras):
                opt_match = re.match(r'^([A-Da-d])[.、]\s*(.*)', paras[j])
                if opt_match:
                    opt_letter = opt_match.group(1).upper()
                    opt_content = opt_match.group(2)
                    options[opt_letter] = opt_content
                    j += 1
                else:
                    break

            questions.append({
                "number": q_num,
                "type": "choice",
                "question": q_text,
                "options": options,
                "answer": answer,
                "raw": p,
            })
            i = j
        else:
            i += 1
    return questions


def _extract_judgment_questions(paras: List[str]) -> List[Dict]:
    """提取纯判断题（课堂练习格式）"""
    questions = []
    i = 0
    while i < len(paras):
        p = paras[i]
        # 匹配 "(n) 判断题" -> 下一行是题目
        m = re.match(r'^[（(](\d+)[）)]\s*判断题', p)
        if m:
            q_num = int(m.group(1))
            if i + 1 < len(paras):
                q_text = paras[i + 1]
                answer_m = re.search(r'[（(]\s*([对错])\s*[）)]', q_text)
                answer = answer_m.group(1) if answer_m else ""
                questions.append({
                    "number": q_num,
                    "type": "judgment",
                    "question": q_text,
                    "answer": answer,
                    "raw": q_text,
                })
                i += 2
            else:
                i += 1
        else:
            i += 1
    return questions


def extract_keywords(question: Dict) -> List[str]:
    """从题目中提取关键词"""
    text = question.get("question", "")
    options = question.get("options", {})
    all_text = text + " " + " ".join(options.values())

    keywords = []

    java_keywords = [
        "标识符", "关键字", "数据类型", "变量", "常量", "运算符",
        "表达式", "语句", "循环", "分支", "条件", "数组",
        "方法", "类", "对象", "继承", "接口", "多态",
        "封装", "构造方法", "重载", "重写", "覆盖",
        "异常", "线程", "流", "IO", "输入输出",
        "String", "StringBuffer", "StringBuilder",
        "ArrayList", "HashMap", "List", "Map", "Set",
        "泛型", "集合", "包装类", "自动装箱", "拆箱",
        "final", "static", "public", "private", "protected",
        "abstract", "interface", "extends", "implements",
        "throws", "throw", "try", "catch", "finally",
        "synchronized", "volatile", "transient",
        "JVM", "JDBC", "SQL", "Unicode", "char",
        "boolean", "int", "float", "double", "long",
        "switch", "case", "default", "break", "continue",
        "return", "if", "else", "while", "for", "do",
        "main", "void", "new", "null", "true", "false",
        "this", "super", "class", "import", "package",
        "scanner", "Math", "random", "Calendar", "Date",
        "InputStream", "OutputStream", "Reader", "Writer",
        "File", "Thread", "Runnable", "sleep", "join",
        "Object", "Comparable", "Comparator", "Iterator",
        "printf", "println", "print", "format",
    ]

    for kw in java_keywords:
        pattern = re.compile(re.escape(kw), re.IGNORECASE)
        if pattern.search(all_text):
            keywords.append(kw)

    return list(set(keywords))  # 去重


def match_questions_to_knowledge(
    questions: List[Dict], knowledge_base: List[Dict]
) -> List[Dict]:
    """
    将题目列表匹配到知识点库（整题文本向量化语义匹配，关键字词表兜底）。

    策略：
    1. 题干+选项整段做向量化语义搜索（可命中"Java注释符号"→"注释"等
       词表外表述，修复原有关键字表漏匹配问题）；
    2. 整题命中不足 5 条时，用关键字词表逐词补充召回。
    """
    from analysis.knowledge_builder import search_knowledge

    results = []
    for q in questions:
        keywords = extract_keywords(q)
        text = q.get("question", "")
        options = q.get("options", {})
        full_text = f"{text} {' '.join(options.values())}".strip()

        matched_knowledge = []
        seen = set()

        def _add_hits(hits) -> bool:
            """把命中的知识点去重加入结果，攒满 5 条返回 True"""
            for hit in hits:
                key = hit.get("视频/知识点名称", "")
                if key and key not in seen:
                    seen.add(key)
                    matched_knowledge.append(hit)
                    if len(matched_knowledge) >= 5:
                        return True
            return False

        # 策略1：整题文本语义搜索
        if full_text:
            _add_hits(search_knowledge(full_text, knowledge_base))

        # 策略2：关键字词表逐词补充
        if len(matched_knowledge) < 5:
            for kw in keywords:
                if _add_hits(search_knowledge(kw, knowledge_base)):
                    break

        results.append({
            "question": q,
            "keywords": keywords,
            "knowledge": matched_knowledge,
        })

    return results


def generate_question_report(matched_questions: List[Dict]) -> str:
    """生成题目-知识点匹配报告"""
    lines = []
    lines.append("=" * 64)
    lines.append("  题目知识点匹配报告")
    lines.append("=" * 64)
    lines.append(f"共解析 {len(matched_questions)} 道题目")
    lines.append("")

    # 按知识点分组
    knowledge_groups = {}
    for mq in matched_questions:
        for kn in mq.get("knowledge", []):
            unit = kn.get("MOOC教学单元", "未分类")
            if unit not in knowledge_groups:
                knowledge_groups[unit] = {"count": 0, "questions": [], "names": set()}
            knowledge_groups[unit]["count"] += 1
            knowledge_groups[unit]["names"].add(kn.get("视频/知识点名称", ""))
            q_text = mq["question"]["question"][:60]
            if q_text not in knowledge_groups[unit]["questions"]:
                knowledge_groups[unit]["questions"].append(q_text)

    if knowledge_groups:
        lines.append("-" * 64)
        lines.append("【题目覆盖的知识点分布】")
        lines.append("-" * 64)
        for unit, info in sorted(
            knowledge_groups.items(), key=lambda x: -x[1]["count"]
        ):
            lines.append(f"  {unit}：{info['count']} 道题")
            for q in info["questions"][:3]:
                lines.append(f"    · {q}")
            if len(info["questions"]) > 3:
                lines.append(f"    ... 共 {len(info['questions'])} 道")
            lines.append("")

    # 未匹配的题目
    unmatched = [mq for mq in matched_questions if not mq.get("knowledge")]
    if unmatched:
        lines.append("-" * 64)
        lines.append("【未匹配到知识点的题目】")
        lines.append("-" * 64)
        for mq in unmatched:
            q = mq["question"]
            lines.append(f"  第{q['number']}题：{q['question'][:60]}")
        lines.append("")

    lines.append("=" * 64)
    lines.append("  报告结束")
    lines.append("=" * 64)
    return "\n".join(lines)
