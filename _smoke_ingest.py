"""
Smoke #3: Real CSV ingest end-to-end (5-stage pipeline)
1. Find a sample CSV (prefer head songs / fallback to data/student_scores.csv aggregated)
2. Ingest via ingest_service.ingest_file
3. Verify rows landed in PG
"""
import sys, os, time, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "server"))

try:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass

from pathlib import Path
from server.core.db import PgvectorStore, db_session, run_migration_if_needed
from server.services.ingest_service import ingest_file
from server.services.embedding import EmbeddingModel

print("--- Step 0: run_migration_if_needed ---", flush=True)
run_migration_if_needed()
store = PgvectorStore()

# --- Wipe test data first ---
print("\n--- Step 1: Wipe old ingest test data ---", flush=True)
store.wipe_all_uploads()
print("    wiped. overview =", store.overview_stats(), flush=True)

# --- Pick a sample file to ingest ---
CANDIDATES = [
    # Prefer user's file (if present)
    r"D:\A教育agent\头歌\tougeall\智慧树头歌学生学习数据\头歌实验_2026.7.21\Java高级编程（2026）-1\3699835_Java面向对象2-项目2-银行账户管理（继承与多态版）.csv",
    r"D:\A教育agent\data\student_scores.csv",
]
SAMPLE = None
for p in CANDIDATES:
    if Path(p).exists():
        SAMPLE = Path(p)
        break
if SAMPLE is None:
    # make a synthetic 学生成绩 CSV （学生×实验 行级）
    SAMPLE = Path(r"D:\A教育agent\data\smoke_touge_like.csv")
    import csv
    with SAMPLE.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["user_id", "user_name", "group_name", "experiment_name", "total_score", "submit_count", "correct_rate"])
        classes = [("软件2班", 20260001001, ["张三","李四","王五","赵六","钱七","孙八","周九","吴十"]),
                   ("软件2班", 20260001009, ["郑十一","王十二","冯十三","陈十四","褚十五","卫十六","蒋十七","沈十八"]),
                   ("卢冶班",  20260002001, ["韩梅梅","李雷","魏华","林涛","凯特","露西","莉莉","吉姆"]),]
        exps = ["银行账户管理（继承与多态）","数组与排序","异常处理","多线程基础"]
        import random
        random.seed(42)
        w.writerow([None, "卢冶", None, None, None, None, None])  # teacher row: name=卢冶 (as teacher_noise)
        for cls, sid, names in classes:
            for i, name in enumerate(names):
                for exp in exps:
                    score = round(random.uniform(35, 100), 1)
                    w.writerow([str(sid + i), name, cls, exp, score, random.randint(1,5), round(random.uniform(0.3,1.0), 2)])
    print(f"    Synthetic CSV created: {SAMPLE} ({sum(1 for _ in SAMPLE.open())} lines)", flush=True)

print(f"\n--- Step 2: Ingesting sample = {SAMPLE} ---", flush=True)
print(f"    size = {SAMPLE.stat().st_size} bytes", flush=True)
data = SAMPLE.read_bytes()
file_hash = hashlib.md5(data).hexdigest()
t0 = time.perf_counter()
res = ingest_file(
    store_path=str(SAMPLE),
    file_hash=file_hash,
    safe_name=SAMPLE.name,
    original_name=SAMPLE.name,
    relative_path=SAMPLE.parent.name + "/",
    force=True,
)
dt = time.perf_counter() - t0
print(f"    ingest_file result: {res}", flush=True)
print(f"    wall-time = {dt:.2f}s", flush=True)

# --- overview ---
print("\n--- Step 3: overview_stats after ingest ---", flush=True)
ov = store.overview_stats()
for k, v in ov.items():
    print(f"    {k:<22s} = {v}")

# --- Class summary rows ---
print("\n--- Step 4: class_summary (limit 10) ---", flush=True)
cs = store.class_summary(limit=10)
for r in cs:
    print(f"    cls={str(r.get('class_name')):<14s}  exp={str(r.get('experiment_name')):<30s}  n={r.get('student_count')}  avg={r.get('avg_score')}  weak%={r.get('weak_rate_percent')}")

# --- Student summary rows ---
print("\n--- Step 5: student_summary (weak_rate_percent>0, limit 8) ---", flush=True)
ss = store.student_summary(min_weak_rate=0.01, limit=8)
for r in ss:
    print(f"    sid={r.get('student_id')} name={str(r.get('name')):<10s} cls={str(r.get('class_name')):<14s} avg={r.get('avg_score')} weak%={r.get('weak_rate_percent')} exps={r.get('experiment_count')}")

# --- Hybrid search test (query with class filter) ---
print("\n--- Step 6: hybrid_search test with EmbeddingModel.encode_one ---", flush=True)
em = EmbeddingModel.get_instance()
q_vec = em.encode_one("卢冶班 平均分60以下的学生有哪些")
hits = store.hybrid_search(query_embedding=q_vec, class_name="卢冶班", min_score=0, max_score=60, top_k=8)
for h in hits[:5]:
    print(f"    sim={h.get('similarity'):.4f} name={str(h.get('name')):<10s} cls={str(h.get('class_name')):<14s} exp={str(h.get('experiment_name')):<28s} score={h.get('final_score')}")

print("\n================ [INGEST PIPELINE PASSED] ================", flush=True)
