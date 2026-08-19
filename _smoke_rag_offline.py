"""
Smoke #4: RAG off-line pipeline (no LLM call)
  - rag_service.parse_intent() — 学号/姓名/班级/实验/分数段 regex 抽取
  - rag_service.build_context() — 调用 Embedding 模型 + PgvectorStore.hybrid_search
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "server"))
try:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_CACHE", r"D:\A教育agent\data\models")
os.environ.setdefault("TRANSFORMERS_CACHE", r"D:\A教育agent\data\models")
os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", r"D:\A教育agent\data\models")

from server.services import rag_service
from server.services.rag_service import build_retrieval_context

QUERIES = [
    "软件1班 60分以下的学生有哪些？",
    "软件1-3班 这次银行账户管理实验的平均分是多少？",
    "陈尚琳 这个学生怎么样？",
    "学号252219605209 成绩多少？",
]

from server.core.db import pg_store
from server.services.embedding import embedding

for q in QUERIES:
    print("\n" + "="*72)
    print(f"[QUERY] {q}")
    try:
        intent = rag_service._extract_intent(q)
        print(f"  => INTENT: filters={intent}")
        ctx_text, q_vec, intent2 = build_retrieval_context(q, top_k_rows=6)
        print(f"  => CTX TEXT LENGTH: {len(ctx_text)} chars")
        try:
            rows = pg_store.hybrid_search(
                q_vec,
                student_id=intent.get("student_id"),
                name=intent.get("name"),
                class_name=intent.get("class_name"),
                experiment_name=intent.get("experiment_name"),
                min_score=intent.get("min_score"),
                max_score=intent.get("max_score"),
                top_k=6,
                vector_only=intent.get("vector_only", False),
            )
        except Exception as e2:
            print(f"  => WARN: hybrid_search standalone failed: {e2}")
            rows = []
        print(f"  => ROWS RETRIEVED: {len(rows)}")
        for i, r in enumerate(rows[:3]):
            name = r.get('name','?')
            cls = r.get('class_name','?')
            exp = (r.get('experiment_name') or '')[:20]
            sc = r.get('final_score')
            sim = r.get('similarity')
            print(f"    #{i+1}  sim={sim:.4f}  {name}/{cls}/{exp} score={sc}")
        if ctx_text:
            print(f"  => FULL CTX SNIPPET (first 600 chars):")
            snippet = ctx_text[:600]
            for line in snippet.splitlines():
                print(f"    {line}")
            if len(ctx_text) > 600:
                print(f"    ... (+{len(ctx_text)-600} chars omitted)")
        try:
            sys_prompt, _, _ = rag_service.build_system_prompt(q)
            print(f"  => SYSTEM PROMPT SNIPPET ({len(sys_prompt)} chars, first 300):")
            for line in sys_prompt[:300].splitlines():
                print(f"    {line}")
            if len(sys_prompt) > 300:
                print(f"    ...")
        except Exception as e3:
            print(f"  => WARN: build_system_prompt failed: {e3}")
    except Exception as e:
        import traceback
        print(f"  => FAIL: {type(e).__name__}: {e}")
        traceback.print_exc(limit=15)

print("\n" + "="*72)
print("[RAG OFFLINE PIPELINE DONE]")
print("[DBG] Check: LLM provider configured? ->", os.environ.get("DEEPSEEK_API_KEY") or "(not set: will use fallback)")
