# _*_ coding : UTF-8 _*_
"""
教学预警系统 - FastAPI 启动入口
对标参考项目：FastAPI-Vue-Admin server/main.py
"""
import os
import sys
import io
from pathlib import Path

if sys.platform.startswith("win"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = BASE_DIR.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(BASE_DIR)


def run():
    import uvicorn
    import yaml

    with open(BASE_DIR / "config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    app_cfg = cfg.get("app", {})
    port = int(app_cfg.get("port", 9090))
    host = str(app_cfg.get("host", "0.0.0.0"))
    reload = bool(app_cfg.get("reload", True))

    print("\n" + "=" * 60)
    print(f"  教学预警系统 FastAPI 启动中...")
    print("=" * 60)
    print(f"  后端 API:   http://localhost:{port}")
    print(f"  接口文档:   http://localhost:{port}/docs")
    print(f"  前端开发:   http://localhost:5173")
    print("=" * 60 + "\n")

    uvicorn.run(
        app="app:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    run()
