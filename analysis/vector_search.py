"""
中文短文本向量化检索（纯标准库实现）。

对中文知识点这类短文本，采用"字符级 n-gram + TF-IDF 权重 + 余弦相似度"
进行语义匹配，无需分词器与任何第三方依赖，纯本地运行：

- "抽象类" 与 "动物抽象类" → 共享特征"抽象""类"，相似度高
- "Java注释符号" 与 "注释" → 共享特征"注释"，可命中
- 查询向量先投影到知识库特征空间（vocab），长查询中的无关字词不会稀释相似度

用法：
    index = build_knowledge_index(knowledge_base)   # 构建一次
    hits = search_semantic(query, index, top_k=5)   # 多次查询
"""

import math
import re
import unicodedata
from collections import Counter
from typing import Dict, List, Tuple

_HANZI_RE = re.compile(r"[\u4e00-\u9fff]")
_WORD_RE = re.compile(r"[a-zA-Z0-9]+")


def tokenize(text: str) -> List[str]:
    """把文本转为字符 n-gram 特征序列（中文单字+相邻双字，英文/数字整词）"""
    text = unicodedata.normalize("NFKC", text or "").lower()
    tokens: List[str] = []
    for m in _WORD_RE.finditer(text):
        tokens.append(m.group(0))
    hanzi = _HANZI_RE.findall(text)
    tokens.extend(hanzi)
    if len(hanzi) >= 2:
        tokens.extend(hanzi[i] + hanzi[i + 1] for i in range(len(hanzi) - 1))
    return tokens


def _term_freq(tokens: List[str]) -> Dict[str, int]:
    return dict(Counter(tokens))


def _cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def build_knowledge_index(knowledge_base: List[Dict]) -> Dict:
    """
    预计算知识库条目向量。

    返回 {"items": [条目...], "vecs": [向量...], "vocab": 特征集合}。
    条目文本 = 视频/知识点名称（主特征）+ MOOC教学单元、项目名称（0.5 降权上下文）。
    """
    docs = []
    for entry in knowledge_base:
        name = entry.get("视频/知识点名称", "") or ""
        if not name.strip():
            continue  # 跳过无名称的空条目，避免污染检索结果
        unit = entry.get("MOOC教学单元", "") or ""
        project = entry.get("项目名称", "") or ""
        docs.append({
            "entry": entry,
            "name_tokens": tokenize(name),
            "ctx_tokens": tokenize(f"{unit} {project}"),
        })

    n = len(docs)
    df: Dict[str, int] = {}
    for d in docs:
        for term in set(d["name_tokens"]) | set(d["ctx_tokens"]):
            df[term] = df.get(term, 0) + 1
    idf = {term: math.log((1.0 + n) / (1.0 + freq)) + 1.0 for term, freq in df.items()}

    vecs, items, vocab = [], [], set()
    for d in docs:
        vec: Dict[str, float] = {}
        for term, freq in _term_freq(d["name_tokens"]).items():
            vec[term] = vec.get(term, 0.0) + freq * idf.get(term, 1.0)
            vocab.add(term)
        for term, freq in _term_freq(d["ctx_tokens"]).items():
            vec[term] = vec.get(term, 0.0) + 0.5 * freq * idf.get(term, 1.0)
            vocab.add(term)
        vecs.append(vec)
        items.append(d["entry"])
    return {"items": items, "vecs": vecs, "vocab": vocab}


def search_semantic(
    query: str,
    index: Dict,
    top_k: int = 5,
    threshold: float = 0.0,
) -> List[Tuple[float, Dict]]:
    """
    检索与 query 最相似的 top_k 条，返回 [(分数, 条目), ...]（分数降序）。

    查询向量投影到知识库特征空间（vocab）后再算余弦，
    长查询中的无关字词不会稀释相似度。
    """
    q_vec = _term_freq(tokenize(query))
    vocab = index.get("vocab")
    if vocab:
        q_vec = {k: v for k, v in q_vec.items() if k in vocab}
    scored = []
    for entry_vec, entry in zip(index["vecs"], index["items"]):
        score = _cosine(q_vec, entry_vec)
        if score > threshold:
            scored.append((score, entry))
    scored.sort(key=lambda x: -x[0])
    return scored[:top_k]
