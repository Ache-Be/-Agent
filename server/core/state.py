# _*_ coding : UTF-8 _*_
"""
全局分析状态 & 共享变量（对应原 Flask app.py 里的模块级全局变量）
使用线程锁保证写操作安全（uvicorn 默认线程模式）
"""
import threading
from typing import Any, Dict, List, Optional

from core.config import config, PROJECT_ROOT, BASE_DIR
from core.logging_setup import logger

# ============== 状态锁 ==============
ANALYSIS_LOCK = threading.RLock()

# ============== 上传文件哈希索引 ==============
file_hashes: Dict[str, str] = {}

# ============== 最近分析结果缓存 ==============
latest_results: List[Dict[str, Any]] = []             # 最近一次上传的文件列表
latest_agg_data: Optional[Dict[str, Any]] = None      # 最近一次聚合分析结果
latest_quiz_results: List[Dict[str, Any]] = []
latest_experiment_results: List[Dict[str, Any]] = []
latest_unit_results: List[Dict[str, Any]] = []
latest_attendance_results: List[Dict[str, Any]] = []
latest_knowledge_results: List[Dict[str, Any]] = []

# ============== 知识库 ==============
knowledge_base: Any = None


def load_knowledge_once():
    """加载知识库到内存（启动时调用一次）"""
    global knowledge_base
    try:
        from analysis.knowledge_builder import load_knowledge_base
        kb_path = config.knowledge_csv
        if kb_path.exists():
            knowledge_base = load_knowledge_base(str(kb_path))
            logger.info("知识库加载完成：%d 条（来自 %s）", len(knowledge_base or []), kb_path.name)
        else:
            logger.warning("知识库文件不存在：%s（请先运行 python main.py build-knowledge）", kb_path)
            knowledge_base = []
    except Exception as e:
        logger.warning("知识库加载失败：%s", e)
        knowledge_base = []
