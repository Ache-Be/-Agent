# _*_ coding : UTF-8 _*_
"""
Embedding 模型封装：本地 sentence-transformers (BAAI/bge-small-zh-v1.5)
特点:
- 全局单例：模型只加载一次，避免重复加载显存/内存占用
- 可切换接口（比如以后换成 openai embedding 只要改 provider）
"""
from __future__ import annotations

import threading
from typing import List, Optional

import numpy as np
from loguru import logger

from core.config import config


class EmbeddingModel:
    _instance: Optional["EmbeddingModel"] = None
    _lock = threading.RLock()

    @classmethod
    def get_instance(cls) -> "EmbeddingModel":
        """Explicit accessor (easier to type than constructor-style singleton)."""
        return cls()

    def __new__(cls) -> "EmbeddingModel":
        # 懒加载单例
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._model = None
                cls._instance._provider = ""
            return cls._instance

    # ------------------------------------------------------------
    def _ensure_loaded(self):
        """Ensure model loaded (thread-safe). First call is slow because of weights download."""
        import os as _os
        # Use domestic HuggingFace mirror (faster in China).
        if not _os.environ.get("HF_ENDPOINT"):
            _os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        with self._lock:
            if self._model is not None and self._provider == config.embedding_provider:
                return
            provider = config.embedding_provider
            if provider == "sentence_transformers":
                from sentence_transformers import SentenceTransformer
                cache_dir = str(config.embedding_cache_dir)
                # Pin HF_HUB_CACHE to the project folder (writeable by sandbox)
                _os.environ.setdefault("HF_HUB_CACHE", cache_dir)
                _os.environ.setdefault("TRANSFORMERS_CACHE", cache_dir)
                _os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", cache_dir)
                model_name = config.embedding_model_name
                logger.info(
                    "Loading sentence-transformers model: {} (cache_dir={}, HF_ENDPOINT={})",
                    model_name, cache_dir, _os.environ.get("HF_ENDPOINT"),
                )
                st = SentenceTransformer(
                    model_name,
                    cache_folder=cache_dir,
                    trust_remote_code=True,
                )
                st.max_seq_length = config.embedding_max_seq_length
                self._model = st
                self._provider = provider
                logger.info(
                    "Embedding model loaded OK. vector_dim={}, max_seq_len={}",
                    config.embedding_vector_dim,
                    st.max_seq_length,
                )
            else:
                raise ValueError(f"Unsupported embedding provider: {provider}")

    # ------------------------------------------------------------
    @property
    def vector_dim(self) -> int:
        return config.embedding_vector_dim

    def encode(self, texts: List[str], batch_size: Optional[int] = None,
               normalize: bool = True) -> List[List[float]]:
        """批量编码文本为 512 维向量（float list）。"""
        self._ensure_loaded()
        if not texts:
            return []
        batch_size = int(batch_size or config.embedding_batch_size)
        if not isinstance(texts, list):
            texts = list(texts)
        # None/空字符串保护
        cleaned: List[str] = []
        for t in texts:
            if not isinstance(t, str):
                t = "" if t is None else str(t)
            cleaned.append(t.strip() or "空内容")
        # BGE 推荐在 encode 时加 normalize=True（余弦距离等价于 L2 距离）
        vectors: np.ndarray = self._model.encode(
            cleaned,
            batch_size=batch_size,
            normalize_embeddings=normalize,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return vectors.tolist()

    def encode_one(self, text: str, normalize: bool = True) -> List[float]:
        return self.encode([text], normalize=normalize)[0]


# 全局实例（注意这里调用 encode 时才会真的 load 模型权重，启动不卡）
embedding = EmbeddingModel()
