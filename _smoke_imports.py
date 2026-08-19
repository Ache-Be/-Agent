"""
Smoke #2: Import chain smoke (no embedding needed)
Tests all new service modules can be imported without error.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "server"))

try:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass

CHECK_MODULES = [
    "server.services.embedding",       # EmbeddingModel singleton (ok to import; won't load model until encode())
    "server.services.ingest_service",  # ingest_file pipeline
    "server.services.rag_service",     # RAG pipeline (intent recognizor + hybrid search + prompt build)
    "server.services.upload_service",  # patched process_upload + wipe_all_uploads
    "server.services.chat_service",    # patched chat_stream with RAG fallback
    "server.api.v1.analytics",         # /analytics/* routes
    "server.api.routers",              # includes analytics router
]

failed = []
for mod in CHECK_MODULES:
    try:
        __import__(mod)
        print(f"[OK]  import {mod}")
    except Exception as e:
        import traceback
        print(f"[FAIL] import {mod} -> {type(e).__name__}: {str(e)[:220]}")
        print("="*72)
        traceback.print_exc(limit=15)
        print("="*72)
        failed.append(mod)
    sys.stdout.flush()

if failed:
    print(f"\n!!! {len(failed)} modules FAILED import: {failed}")
    sys.exit(1)
print("\n================ [ALL IMPORTS OK] ================")
