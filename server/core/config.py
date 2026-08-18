# _*_ coding : UTF-8 _*_
"""
配置加载：从 config.yaml 读取，全局单例
"""
import yaml
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).parent.parent.resolve()
PROJECT_ROOT = BASE_DIR.parent
CONFIG_PATH = BASE_DIR / "config.yaml"


class _Config:
    def __init__(self):
        self._data: Dict[str, Any] = {}
        self._load()

    def _load(self):
        if not CONFIG_PATH.exists():
            raise FileNotFoundError(f"配置文件不存在：{CONFIG_PATH}")
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            self._data = yaml.safe_load(f) or {}

    def reload(self):
        self._load()

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        d = self._data
        for k in keys:
            if isinstance(d, dict) and k in d:
                d = d[k]
            else:
                return default
        return d

    # ---- 常用配置快捷访问 ----
    @property
    def BASE_DIR(self) -> Path:
        return BASE_DIR

    @property
    def PROJECT_ROOT(self) -> Path:
        return PROJECT_ROOT

    def app(self) -> Dict[str, Any]:
        return self.get("app", {}) or {}

    @property
    def api_prefix(self) -> str:
        return str(self.get("app.api_prefix", "/api"))

    @property
    def secret_key(self) -> str:
        return str(self.get("app.secret_key", "dev-secret"))

    @property
    def port(self) -> int:
        return int(self.get("app.port", 9090))

    @property
    def host(self) -> str:
        return str(self.get("app.host", "0.0.0.0"))

    @property
    def upload_dir(self) -> Path:
        p = BASE_DIR / str(self.get("upload.upload_dir", "uploads"))
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def report_dir(self) -> Path:
        p = BASE_DIR / str(self.get("upload.report_dir", "uploads/reports"))
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def temp_dir(self) -> Path:
        p = BASE_DIR / str(self.get("upload.temp_dir", "uploads/_temp"))
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def log_dir(self) -> Path:
        p = BASE_DIR / str(self.get("logging.dir", "logs"))
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def allow_ext(self) -> List[str]:
        return list(self.get("upload.allow_ext", [".csv", ".docx", ".xlsx"]))

    @property
    def cors_origins(self) -> List[str]:
        return list(self.get("cors.origins", ["*"]))

    @property
    def knowledge_csv(self) -> Path:
        # config.yaml 里是相对 server/ 的路径，要解析
        raw = self.get("knowledge.csv_path", "../data/knowledge/knowledge_base.csv")
        p = (BASE_DIR / raw).resolve()
        return p

    @property
    def knowledge_backup_dir(self) -> Path:
        raw = self.get("knowledge.backup_dir", "../data/knowledge/backups")
        p = (BASE_DIR / raw).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p


config = _Config()
