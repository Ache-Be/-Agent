"""
统一配置：薄弱判定等分析参数。

配置来源：web/config/settings.json（与 Web 系统共用），未配置时使用默认值。
修改配置后需重启服务/重新运行分析才生效。
"""

import json
from pathlib import Path
from typing import Dict

DEFAULT_CONFIG = {
    # 薄弱判定阈值：子任务得分率/正确率低于此值视为薄弱
    "weak_threshold": 0.7,
    # 头歌实验低分线（final_score 低于此值计入低分）
    "low_score_line": 60,
    # 子任务查答案率超过此值提示关注
    "view_answer_alert_rate": 0.3,
    # 排除名单：这些姓名不计入学生（教师/管理员），可自行增减
    "exclude_names": ["卢冶"],
}

# 与 Web 端共用同一个配置文件
CONFIG_FILE = Path(__file__).resolve().parent.parent / "web" / "config" / "settings.json"

_cache: Dict = {}


def load_config() -> Dict:
    """加载配置：默认值与 settings.json 中的数值项合并（模块级缓存）。"""
    if _cache:
        return _cache
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            for key in cfg:
                val = data.get(key)
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    cfg[key] = val
                elif isinstance(val, list) and key == "exclude_names":
                    # 排除名单：过滤空项，统一为字符串
                    cfg[key] = [str(v).strip() for v in val if str(v).strip()]
    except (OSError, json.JSONDecodeError):
        # 配置文件异常时静默使用默认值，不影响分析主流程
        pass
    _cache.update(cfg)
    return _cache
