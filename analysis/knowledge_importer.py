"""
知识点文档导入器。

从 PDF / Word(.docx) 文档中提取知识点标题行,
增量合并进 data/knowledge/knowledge_base.csv,实现"知识库随上传更新"。

提取策略：
- PDF：用 pypdf 逐页提取文本，按"编号开头"的行识别标题（如 "1.1 Java概述"、
  "8.2 数组遍历"）；扫描版 PDF（无文本层）无法提取，会提示空结果。
- Word：优先取 Heading 样式段落，其次取编号开头的段落。
- 去重：复用 knowledge_builder._normalize_knowledge_name 的归一化规则，
  与现有 MOOC 章节表条目统一比较，实现跨来源去重。
"""

import csv
import logging
import re
import unicodedata
from pathlib import Path

from analysis.knowledge_builder import _normalize_knowledge_name

logger = logging.getLogger(__name__)

DEFAULT_CSV = Path(__file__).resolve().parent.parent / "data" / "knowledge" / "knowledge_base.csv"

# 编号开头行："1"、"1."、"1.1"、"8.2 "、"第3章"、"一、" 等
_NUM_HEADING_RE = re.compile(
    r"^\s*(\d+(?:\.\d+){0,3}|[一二三四五六七八九十]+(?:章|节|讲)|第[一二三四五六七八九十\d]+[章节讲课单元篇])\s*[\.、．:：]?\s*(.+?)\s*$"
)

# 正文干扰行（页码、日期、版权、无意义短行）
_SKIP_RE = re.compile(
    r"^(第\s*\d+\s*页|\d+\s*/\s*\d+|[\d-]+$|copyright|©|版权所有|目录|目\s*录|页码|contents?)$",
    re.IGNORECASE,
)

# 练习题题干特征（避免练习题/习题文档的题干行被误提取为知识点）
_OPTION_LINE_RE = re.compile(r"^[A-Da-d][.、．]\s*")
_QUESTION_MARK_RE = re.compile(
    r"[（(]\s*(?:正确|错误|对|错|A|B|C|D|a|b|c|d|×|√)\s*[）)]"   # （正确）（A）（×）
    r"|[（(]\s*[）)]"                                            # （ ）
    r"|单选题|多选题|判断题|填空题|简答题|论述题|应用题|编程题|选择题|问答题"
    r"|以下\S{0,6}(?:正确|错误|说法|属于|是|符合)|下列"
    r"|不正确的是|错误的是|正确的是"
    r"|选项|答案[:：]?|解析[:：]?"
)


def _is_question_line(text: str) -> bool:
    """判断一行是否更像练习题题干/选项，而非知识点标题"""
    if _OPTION_LINE_RE.match(text):
        return True
    if _QUESTION_MARK_RE.search(text):
        return True
    # 以句号结尾的陈述句：知识点标题几乎不以句号结尾，判断题题干常用
    return text.endswith(("。", "."))


def _clean_title(raw: str) -> str:
    """清洗标题行：去掉行首装饰符、多余空白，并做 NFKC 归一化（康熙部首→标准汉字）"""
    t = re.sub(r"^\s*[·•●▪►-]+\s*", "", raw).strip()
    return unicodedata.normalize("NFKC", t)


def _is_valid_title(text: str) -> bool:
    if len(text) < 2 or len(text) > 60:
        return False
    if _SKIP_RE.match(text):
        return False
    return True


def extract_pdf_headings(path) -> list:
    """从 PDF 提取带编号的标题行，返回形如 '1.1 Java概述' 的名称列表（同编号去重）"""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    lines = []
    for page in reader.pages:
        text = page.extract_text() or ""
        lines.extend(text.splitlines())

    seen = set()
    names = []
    for ln in lines:
        m = _NUM_HEADING_RE.match(ln)
        if not m:
            continue
        num, title = m.group(1), m.group(2)
        title = _clean_title(title)
        if not _is_valid_title(title) or _is_question_line(title):
            continue
        text = f"{num} {title}"
        if num in seen:
            continue
        seen.add(num)
        names.append(text)
    return names


# 知识图谱型 PDF（智慧树导出）中的层级标记与类型词
_GRAPH_MARKERS = {"知识模块", "知识单元"}
_TYPE_WORDS = ("概念性知识", "程序性知识", "思政点", "重点", "难点", "实验", "训练", "任务")


def _strip_type_suffix(s: str) -> str:
    """去掉行尾/行内混入的类型标注词，如 'Java 简要介绍 概念性知识' → 'Java 简要介绍'"""
    for w in _TYPE_WORDS:
        if w in s:
            s = s[: s.find(w)]
    return s.strip(" 　：:、-—")


def extract_pdf_graph(path) -> list:
    """
    从知识图谱型 PDF（智慧树课程知识图谱导出）提取知识点。

    结构示例：
        知识模块
        阶段一 开发环境构建
        知识单元
        Java 简要介绍
        概念性知识
        ...

    返回条目列表（dict），"视频/知识点名称"为知识点名，
    "MOOC教学单元"记录所属模块（如"阶段一 开发环境构建"）。
    """
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    lines = []
    for page in reader.pages:
        text = page.extract_text() or ""
        lines.extend(text.splitlines())

    entries = []
    seen = set()
    current_module = ""
    pending = None  # None / "module" / "knowledge"
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s in _GRAPH_MARKERS:
            pending = "module" if s == "知识模块" else "knowledge"
            continue
        if pending == "module":
            current_module = _clean_title(s)
            pending = None
            continue
        if pending == "knowledge":
            title = _strip_type_suffix(_clean_title(s))
            if _is_valid_title(title):
                key = _normalize_knowledge_name(title)
                if key and key not in seen:
                    seen.add(key)
                    entries.append({
                        "MOOC教学单元": current_module,
                        "视频/知识点名称": title,
                    })
            pending = None
            continue
    return entries


def extract_docx_headings(path) -> list:
    """从 Word 提取标题段落：Heading 样式优先，其次编号开头（整行去重）"""
    from docx import Document

    doc = Document(str(path))
    names = []
    seen = set()
    for p in doc.paragraphs:
        t = p.text.strip()
        if not t or not _is_valid_title(t):
            continue
        style = (p.style.name or "") if p.style else ""
        if "heading" in style.lower():
            candidate = t
        else:
            m = _NUM_HEADING_RE.match(t)
            if not m:
                continue
            candidate = f"{m.group(1)} {_clean_title(m.group(2))}"
        if _is_question_line(candidate):
            continue
        key = _normalize_knowledge_name(candidate)
        if key and key not in seen:
            seen.add(key)
            names.append(candidate)
    return names


def load_existing_rows(csv_path=None) -> (list, list):
    """读取现有知识库 CSV，返回 (行列表, 列名列表)；文件不存在时返回空"""
    csv_path = Path(csv_path) if csv_path else DEFAULT_CSV
    if not csv_path.exists():
        return [], [
            "知识编号", "教学层次", "知识领域", "MOOC教学单元",
            "项目名称", "视频/知识点名称", "视频时长", "知识类型", "难度标签",
        ]
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    return rows, fieldnames


def merge_into_knowledge_base(names, csv_path=None, source="文档导入"):
    """
    把知识点名称列表增量合并进知识库 CSV。

    返回 (新增条数, 跳过条数, 当前总条数)。
    去重规则：归一化后的"视频/知识点名称"相同则跳过；
    已有条目的元数据（教学层次/单元/项目）不覆盖，保持完整。
    """
    csv_path = Path(csv_path) if csv_path else DEFAULT_CSV
    rows, fieldnames = load_existing_rows(csv_path)

    existing = {
        _normalize_knowledge_name(r.get("视频/知识点名称", ""))
        for r in rows
        if r.get("视频/知识点名称")
    }
    max_no = 0
    for r in rows:
        v = (r.get("知识编号") or "").strip()
        if v.isdigit():
            max_no = max(max_no, int(v))

    added, skipped = 0, 0
    for item in names:
        # 支持 str 或 dict；dict 可携带 MOOC教学单元 等元数据
        if isinstance(item, dict):
            name = str(item.get("视频/知识点名称") or "").strip()
            unit = str(item.get("MOOC教学单元") or "")
        else:
            name = str(item).strip()
            unit = ""
        key = _normalize_knowledge_name(name)
        if not key or key in existing:
            skipped += 1
            continue
        max_no += 1
        rows.append({
            "知识编号": str(max_no),
            "教学层次": "",
            "知识领域": "",
            "MOOC教学单元": unit,
            "项目名称": "",
            "视频/知识点名称": name,
            "视频时长": "",
            "知识类型": source,
            "难度标签": "",
        })
        existing.add(key)
        added += 1

    if added:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    return added, skipped, len(rows)


def import_knowledge_file(path, csv_path=None, source=None):
    """
    导入单个知识点文档（PDF / .docx）。

    返回 (新增条数, 跳过条数, 提取到的名称列表)。
    source 记录条目来源（写入"知识类型"列）。
    """
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        names = extract_pdf_headings(path)
        if not names:
            # 编号大纲型没提取到 → 尝试智慧树知识图谱型
            names = extract_pdf_graph(path)
    elif ext == ".docx":
        names = extract_docx_headings(path)
    else:
        raise ValueError(f"不支持的知识点文档类型: {ext}")

    if not names:
        logger.warning("知识点文档未提取到标题行: %s", path)
        return 0, 0, []

    source = source or f"文档导入:{path.stem}"
    added, skipped, total = merge_into_knowledge_base(names, csv_path=csv_path, source=source)
    logger.info("知识点导入 %s: 新增 %d, 跳过 %d, 库内共 %d 条", path.name, added, skipped, total)
    return added, skipped, names
