"""
Smoke #1: PgvectorStore DB connectivity (no embedding needed)
Tests:
  - engine/Session can connect
  - run_migration_if_needed (re-runs 001_init.sql idempotent)
  - overview_stats (returns empty dict, no data expected yet)
  - upsert_uploaded_file (file_hash dedup) + bulk_upsert_student_rows (embedding column populated with zeros)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "server"))

try:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass

from server.core.db import PgvectorStore, run_migration_if_needed, db_session
from server.core.config import config as cfg

print(f"[CFG] build_db_url = {cfg.build_db_url()}", flush=True)

print("\n--- Step 1: run_migration_if_needed() ---", flush=True)
try:
    run_migration_if_needed()
    print("[OK] migration idempotent", flush=True)
except Exception as e:
    print(f"[FAIL] migration -> {type(e).__name__}: {e}", flush=True)
    import traceback; traceback.print_exc(); sys.exit(1)

store = PgvectorStore()

print("\n--- Step 2: overview_stats (empty) ---", flush=True)
try:
    stats = store.overview_stats()
    print(f"[OK] overview_stats = {stats}", flush=True)
except Exception as e:
    print(f"[FAIL] overview_stats -> {type(e).__name__}: {e}", flush=True)
    import traceback; traceback.print_exc(); sys.exit(2)

print("\n--- Step 3: upsert_uploaded_file (file_hash dedup) ---", flush=True)
try:
    f1 = store.upsert_uploaded_file(
        file_hash = "d41d8cd98f00b204e9800998ecf8427e",  # md5("")
        safe_name = "smoke_test.csv",
        original_name = "smoke_test.csv",
        relative_path = "smoke/",
        file_size = 1234,
        source_type = "touge",
        experiment_name = "smoke_experiment",
    )
    print(f"[OK] upsert_uploaded_file -> file_id={f1.id} status={f1.status}", flush=True)
    f2 = store.upsert_uploaded_file(
        file_hash = "d41d8cd98f00b204e9800998ecf8427e",   # same hash -> dedup
        safe_name = "smoke_test_dup.csv",
        original_name = "smoke_test_dup.csv",
        relative_path = "smoke/",
        file_size = 1234,
        source_type = "touge",
        experiment_name = "smoke_experiment",
    )
    assert f2.id == f1.id, f"dedup failed: {f2.id} != {f1.id}"
    print(f"[OK] dedup ok (same file_id={f2.id})", flush=True)
except Exception as e:
    print(f"[FAIL] upsert_uploaded_file -> {type(e).__name__}: {e}", flush=True)
    import traceback; traceback.print_exc(); sys.exit(3)

print("\n--- Step 4: mark_uploaded_rows + bulk_upsert_student_rows (fake 512-dim zeros embedding) ---", flush=True)
try:
    V = 512
    rows = [
        {"line_no": 1, "row_type": "student",
         "student_id": "2026000001", "name": "张三", "class_name": "软件2班",
         "experiment_name": "smoke_experiment", "source_type": "touge",
         "final_score": 88.5, "weak_count": 0, "task_count": 10,
         "row_text": "软件2班 张三(学号2026000001) 实验 smoke_experiment 得分88.5分",
         "extra_cols": {"a": 1}, "embedding": [0.123]*V},
        {"line_no": 2, "row_type": "student",
         "student_id": "2026000002", "name": "李四", "class_name": "软件2班",
         "experiment_name": "smoke_experiment", "source_type": "touge",
         "final_score": 55.0, "weak_count": 1, "task_count": 10,
         "row_text": "软件2班 李四(学号2026000002) 实验 smoke_experiment 得分55.0分",
         "extra_cols": {"b": 2}, "embedding": [0.456]*V},
    ]
    store.bulk_upsert_student_rows(f1.id, rows, vector_dim=V)
    store.mark_uploaded_rows(f1.id, rows_total=5, rows_student=2, rows_noise=2, rows_teacher=1)
    print("[OK] bulk_upsert_student_rows + mark_uploaded_rows", flush=True)
except Exception as e:
    print(f"[FAIL] bulk_upsert -> {type(e).__name__}: {e}", flush=True)
    import traceback; traceback.print_exc(); sys.exit(4)

print("\n--- Step 5: overview_stats after 2 students ---", flush=True)
try:
    stats2 = store.overview_stats()
    print(f"[OK] overview_stats = {stats2}", flush=True)
    assert stats2["student_count"] >= 2
except Exception as e:
    print(f"[FAIL] stats2 -> {type(e).__name__}: {e}", flush=True)
    import traceback; traceback.print_exc(); sys.exit(5)

print("\n--- Step 6: hybrid_search (query_embedding zeros -> should return rows sorted) ---", flush=True)
try:
    hits = store.hybrid_search(
        query_embedding=[0.0]*V,
        class_name="软件2班",
        top_k=10,
    )
    print(f"[OK] hybrid_search returned {len(hits)} hits", flush=True)
    for h in hits[:3]:
        print(f"     name={h['name']} score={h['final_score']} similarity={h.get('similarity')}", flush=True)
except Exception as e:
    print(f"[FAIL] hybrid_search -> {type(e).__name__}: {e}", flush=True)
    import traceback; traceback.print_exc(); sys.exit(6)

print("\n--- Step 7: student_summary / class_summary views ---", flush=True)
try:
    ss = store.student_summary(class_name="软件2班", limit=5)
    cs = store.class_summary(class_name="软件2班", limit=5)
    print(f"[OK] student_summary rows={len(ss)} : first={ss[0] if ss else None}", flush=True)
    print(f"[OK] class_summary rows={len(cs)} : first={cs[0] if cs else None}", flush=True)
except Exception as e:
    print(f"[FAIL] views -> {type(e).__name__}: {e}", flush=True)
    import traceback; traceback.print_exc(); sys.exit(7)

print("\n--- Step 8: wipe smoke data (keep empty DB clean) ---", flush=True)
try:
    store.wipe_all_uploads()
    stats_final = store.overview_stats()
    print(f"[OK] after wipe -> {stats_final}", flush=True)
except Exception as e:
    print(f"[FAIL] wipe -> {type(e).__name__}: {e}", flush=True)
    import traceback; traceback.print_exc(); sys.exit(8)

print("\n================ [DB SMOKE PASSED] ================", flush=True)
