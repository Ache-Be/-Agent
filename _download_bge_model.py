"""
Download BAAI/bge-small-zh-v1.5 to local project data/models/bge-small-zh-v1.5
(8 mandatory files for SentenceTransformer:
  config.json          - model.safetensors (weight, pytorch_model.bin backup)
  tokenizer.json / vocab.txt / tokenizer_config.json / special_tokens_map.json
  sentence_bert_config.json / modules.json / 1_Pooling/config.json
  config_sentence_transformers.json / README.md / sentence_bert_config.json sentence
We download from HF mirror
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass

from pathlib import Path
import urllib.request, json

MODEL_ID = "BAAI/bge-small-zh-v1.5"
BASE = f"https://hf-mirror.com/{MODEL_ID}/resolve/main/"
LOCAL_DIR = Path(r"D:\A教育agent\data\models\bge-small-zh-v1.5")
LOCAL_1POOL = LOCAL_DIR / "1_Pooling"

LOCAL_DIR.mkdir(parents=True, exist_ok=True)
LOCAL_1POOL.mkdir(parents=True, exist_ok=True)

FILES = {
    LOCAL_DIR / "README.md": f"{BASE}README.md",
    LOCAL_DIR / "config.json": f"{BASE}config.json",
    LOCAL_DIR / "tokenizer_config.json": f"{BASE}tokenizer_config.json",
    LOCAL_DIR / "special_tokens_map.json": f"{BASE}special_tokens_map.json",
    LOCAL_DIR / "vocab.txt": f"{BASE}vocab.txt",
    LOCAL_DIR / "tokenizer.json": f"{BASE}tokenizer.json",
    LOCAL_DIR / "sentence_bert_config.json": f"{BASE}sentence_bert_config.json",
    LOCAL_DIR / "config_sentence_transformers.json": f"{BASE}config_sentence_transformers.json",
    LOCAL_DIR / "modules.json": f"{BASE}modules.json",
    LOCAL_DIR / "model.safetensors": f"{BASE}model.safetensors",
    LOCAL_1POOL / "config.json": f"{BASE}1_Pooling/config.json",
}

total = 0
ok = 0
for target, url in FILES.items():
    total += 1
    # skip if exists and size > small (vocab/tokenizer/model weight OK for a （权重 >1KB ）
    if target.exists():
        if target.stat().st_size < 1024:  # too small, probably corrupt
            target.unlink(missing_ok=True)
        else:
            print(f"[SKIP] {target.name} ({target.stat().st_size} bytes")
            ok += 1
            continue
    print(f"[DL  ] {url} -> {target} ...", flush=True)
    try:
        # progress bar for big files
        def reporthook(block_num, block_size, total_size):
            downloaded = block_num * block_size
            if total_size > 0:
                pct = 100 * downloaded / total_size
                sys.stdout.write(f"\r       {target.name}: {downloaded//1024}KB / {total_size//1024}KB ({pct:.1f}%)")
                sys.stdout.flush()
        urllib.request.urlretrieve(url, str(target), reporthook=reporthook if target.name == 'model.safetensors' else None)
    except Exception as e:
        print(f"\r[ERR] {target.name}: {type(e).__name__}: {e}")
        continue
    print(f"\r[OK  ] {target.name} ({target.stat().st_size} bytes         ")
    ok += 1

print(f"\n=== RESULT: {ok}/{total} files ready. local model dir at: {LOCAL_DIR}")
print(f"Then change embedding cache dir = {LOCAL_DIR}")
