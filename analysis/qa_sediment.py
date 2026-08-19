"""
问答沉淀库（QA Sediment）。

把每次 AI 问答对（问题、回答、命中的知识点、时间、会话）落盘到
data/qa_sediment/qa_logs.jsonl，形成"越问越准"的闭环：
- 沉淀：AI 回答完成后自动保存问答对；
- 复用：新问题进来时，先从历史问答中检索相似问题，把之前的高质量回答
  作为参考注入 prompt，让 AI 站在过去的经验上回答；
- 查看：Web 端可浏览沉淀记录，作为教学经验沉淀。

注意：问答沉淀库与知识点库（data/knowledge/knowledge_base.csv）相互独立，
AI 的回答不直接写入知识点库，避免污染权威知识来源（知识库只由上传的
权威文档扩充）。
"""

import json
import logging
import re
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
QA_DIR = ROOT / "data" / "qa_sediment"
QA_LOG_FILE = QA_DIR / "qa_logs.jsonl"

_MAX_ANSWER_LEN = 600  # 注入 prompt 时回答的截断长度
_MAX_QA_RECORDS = 3000  # 沉淀库上限：超过后裁掉最旧的记录，防止无限膨胀、拖慢检索


def _prune_qa_file():
    """沉淀库超过上限时保留最新 _MAX_QA_RECORDS 条。
    注意：需全量重写文件（O(n)，n=3000 量级，仅超限后每次保存触发一次），
    文件本身是追加写，按行序保留最新部分即可保持时间顺序。
    """
    try:
        with open(QA_LOG_FILE, "r", encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        if len(lines) <= _MAX_QA_RECORDS:
            return
        with open(QA_LOG_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines[-_MAX_QA_RECORDS:])
        logger.info("问答沉淀裁剪：%d -> %d 条", len(lines), _MAX_QA_RECORDS)
    except Exception as e:
        logger.warning("问答沉淀裁剪失败: %s", e)


def _norm_text(s: str) -> str:
    """归一化：NFKC（康熙部首→标准汉字）+ 去空白与标点（与 ai_assistant 一致）"""
    s = unicodedata.normalize("NFKC", str(s or ""))
    return re.sub(r"[\s,，。.．、;；:：()（）【】\[\]'\"“”‘’\-—_/\\|]+", "", s)


def _bigrams(s: str) -> set:
    """中文字符二元组集合，用于轻量相似度"""
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else set()


def _ensure_dir():
    QA_DIR.mkdir(parents=True, exist_ok=True)


def save_qa(
    question: str,
    answer: str,
    hit_knowledge: Optional[List[str]] = None,
    conversation_id: str = "",
) -> Optional[Dict]:
    """保存一条问答沉淀记录，返回写入的记录；问题或回答为空时返回 None。"""
    try:
        _ensure_dir()
        question = (question or "").strip()
        answer = (answer or "").strip()
        if not question or not answer:
            return None
        rec = {
            "id": uuid.uuid4().hex[:12],
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "conversation_id": conversation_id or "",
            "question": question,
            "answer": answer,
            "hit_knowledge": [k for k in (hit_knowledge or []) if k],
        }
        with open(QA_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _prune_qa_file()
        return rec
    except Exception as e:
        logger.warning("保存问答沉淀失败: %s", e)
        return None


def load_qa_logs(limit: int = 100) -> List[Dict]:
    """读取沉淀记录，按时间倒序（最新在前）。"""
    if not QA_LOG_FILE.exists():
        return []
    logs = []
    try:
        with open(QA_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    logs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.warning("读取问答沉淀失败: %s", e)
        return []
    logs.sort(key=lambda r: r.get("time", ""), reverse=True)
    return logs[:limit]


def count_qa() -> int:
    """沉淀记录总数。"""
    if not QA_LOG_FILE.exists():
        return 0
    n = 0
    try:
        with open(QA_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    n += 1
    except Exception:
        pass
    return n


def clear_qa() -> int:
    """清空问答沉淀 jsonl 文件，返回删除条数。失败返回 0（不抛出）。"""
    if not QA_LOG_FILE.exists():
        return 0
    n = count_qa()
    try:
        QA_LOG_FILE.write_text("", encoding="utf-8")
        logger.info("问答沉淀 jsonl 已清空：%d 条", n)
        return n
    except Exception as e:
        logger.warning("问答沉淀 jsonl 清空失败: %s", e)
        return 0


def delete_qa_by_ids(ids) -> int:
    """按 id 逐条删除 jsonl 沉淀，返回删除条数。失败返回 0（不抛出）。"""
    if not ids:
        return 0
    if not QA_LOG_FILE.exists():
        return 0
    id_set = {str(i) for i in ids}
    try:
        with open(QA_LOG_FILE, "r", encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        keep, deleted = [], 0
        for ln in lines:
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                keep.append(ln)  # 坏行不删，保留原样
                continue
            if str(rec.get("id") or "") in id_set:
                deleted += 1
            else:
                keep.append(ln)
        if deleted:
            with open(QA_LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(keep)
            logger.info("问答沉淀 jsonl 逐条删除：%d 条", deleted)
        return deleted
    except Exception as e:
        logger.warning("问答沉淀 jsonl 逐条删除失败: %s", e)
        return 0


def retrieve_qa(query: str, top_k: int = 2) -> List[Dict]:
    """从历史问答中检索与当前问题最相似的记录。

    与 retrieve_knowledge 相同的轻量打分思路：比较归一化后的"问题"字段，
    包含关系 + bigram 重叠。空库或无关时返回空列表。
    """
    if not query:
        return []
    q = _norm_text(query)
    if len(q) < 2:
        return []
    q_bg = _bigrams(q)
    scored = []
    for rec in load_qa_logs(limit=500):
        nq = _norm_text(str(rec.get("question") or ""))
        if not nq:
            continue
        score = 0.0
        # 1) 包含关系（最可靠）
        if q in nq or nq in q:
            score += 10.0
        # 2) 问题字段 bigram 重叠
        r_bg = _bigrams(nq)
        if r_bg and q_bg:
            inter = len(q_bg & r_bg)
            score += 4.0 * inter / max(len(q_bg), len(r_bg))
        if score > 0:
            scored.append((score, rec))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [rec for _, rec in scored[:top_k]]


def format_qa_ref(records: List[Dict], max_answer_len: int = _MAX_ANSWER_LEN) -> str:
    """把历史问答格式化为注入 prompt 的参考文本。"""
    if not records:
        return ""
    lines = [
        "\n## 历史相似问答（团队此前的教学问答沉淀，供参考）\n"
        "仅作为回答风格与经验参考，若与当前数据分析结果冲突，以最新数据为准："
    ]
    for rec in records:
        q = str(rec.get("question") or "").strip()
        a = str(rec.get("answer") or "").strip()
        if not q or not a:
            continue
        if len(a) > max_answer_len:
            a = a[:max_answer_len] + "…"
        lines.append(f"- 问：{q}")
        lines.append(f"  答：{a}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)
