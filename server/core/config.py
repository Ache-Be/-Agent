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
        raw = self.get("knowledge.csv_path", "../data/knowledge/knowledge_base.csv")
        p = (BASE_DIR / raw).resolve()
        return p

    @property
    def knowledge_backup_dir(self) -> Path:
        raw = self.get("knowledge.backup_dir", "../data/knowledge/backups")
        p = (BASE_DIR / raw).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ========== 新增：PostgreSQL + pgvector ==========
    @property
    def db_host(self) -> str:
        return str(self.get("database.host", "127.0.0.1"))

    @property
    def db_port(self) -> int:
        return int(self.get("database.port", 5432))

    @property
    def db_user(self) -> str:
        return str(self.get("database.user", "postgres"))

    @property
    def db_password(self) -> str:
        return str(self.get("database.password", ""))

    @property
    def db_database(self) -> str:
        return str(self.get("database.database", "teaching_warning"))

    @property
    def db_pool_size(self) -> int:
        return int(self.get("database.pool_size", 10))

    @property
    def db_max_overflow(self) -> int:
        return int(self.get("database.max_overflow", 20))

    @property
    def db_pool_timeout(self) -> int:
        return int(self.get("database.pool_timeout", 30))

    @property
    def db_echo(self) -> bool:
        return bool(self.get("database.echo", False))

    def build_db_url(self, driver: str = "postgresql+psycopg2") -> str:
        from urllib.parse import quote_plus
        pwd = quote_plus(self.db_password) if self.db_password else ""
        auth = f"{self.db_user}:{pwd}@" if pwd else f"{self.db_user}@"
        return f"{driver}://{auth}{self.db_host}:{self.db_port}/{self.db_database}"

    # ========== 新增：Embedding 配置 ==========
    @property
    def embedding_provider(self) -> str:
        return str(self.get("embedding.provider", "sentence_transformers"))

    @property
    def embedding_model_name(self) -> str:
        return str(self.get("embedding.model_name", "BAAI/bge-small-zh-v1.5"))

    @property
    def embedding_vector_dim(self) -> int:
        return int(self.get("embedding.vector_dim", 512))

    @property
    def embedding_max_seq_length(self) -> int:
        return int(self.get("embedding.max_seq_length", 512))

    @property
    def embedding_batch_size(self) -> int:
        return int(self.get("embedding.batch_size", 64))

    @property
    def embedding_cache_dir(self) -> Path:
        raw = self.get("embedding.cache_dir", "../data/models")
        p = (BASE_DIR / raw).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ========== 新增：数据清洗规则 ==========
    @property
    def cleaning_teacher_names(self) -> List[str]:
        return [str(x) for x in (self.get("cleaning.teacher_names", []) or [])]

    @property
    def cleaning_noise_keywords(self) -> List[str]:
        return [str(x) for x in (self.get("cleaning.noise_keywords", []) or [])]

    @property
    def cleaning_student_id_regex(self) -> List[str]:
        return [str(x) for x in (self.get("cleaning.student_id_regex", []) or [])]

    @property
    def cleaning_name_len_range(self) -> tuple:
        r = self.get("cleaning.name_len_range", [2, 4])
        return (int(r[0]), int(r[1]))


config = _Config()

