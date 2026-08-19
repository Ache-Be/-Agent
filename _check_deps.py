import sys
try:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass

print("=== sentence-transformers / torch check ===", flush=True)

checks = [
    ("sentence_transformers", "sentence_transformers"),
    ("torch", "torch"),
    ("transformers", "transformers"),
    ("psycopg2", "psycopg2"),
    ("sqlalchemy", "sqlalchemy"),
    ("pgvector", "pgvector"),
    ("pandas", "pandas"),
    ("numpy", "numpy"),
]

for import_name, display in checks:
    try:
        m = __import__(import_name)
        v = getattr(m, "__version__", "?")
        print(f"[OK]  {display:<22s}  version = {v}")
    except Exception as e:
        print(f"[MISS] {display:<22s}  -> {type(e).__name__}: {str(e)[:200]}")
    sys.stdout.flush()
