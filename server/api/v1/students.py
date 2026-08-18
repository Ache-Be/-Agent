# _*_ coding : UTF-8 _*_
"""
学生画像中心 API：学生列表 / 画像详情 / 画像统计
"""
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from core import state
from core.config import config
from core.logging_setup import logger
from core.utils import make_student_key, safe_upload_path

router = APIRouter(prefix="/students", tags=["学生画像中心"])


# ============================================================
# 辅助：字段兼容 + 数据结构统一
# ============================================================

def _agg():
    return state.latest_agg_data or {}


def _sl() -> List[Dict[str, Any]]:
    """student_list（在 list 卡片层直接用），附加 weakness_rate = weak_rate 别名"""
    sl = (_agg().get("student_list") or [])[:]
    out = []
    for s in sl:
        x = dict(s)
        # weakness_rate ← weak_rate
        if "weakness_rate" not in x or x.get("weakness_rate") in (None, 0):
            x["weakness_rate"] = x.get("weak_rate") or 0
        x.setdefault("avg_score", x.get("final_score") or 0)
        x.setdefault("weak_subtask_count", x.get("weak_count") or 0)
        x.setdefault("weak_knowledge_count", x.get("weak_knowledge_count") or 0)
        x.setdefault("_key", x.get("_key") or make_student_key(x.get("name", ""), x.get("student_id", "")))
        out.append(x)
    return out


def _students_portrait_map() -> Dict[str, Dict[str, Any]]:
    """把 cross_analyzer 里 students 字典的奇怪 key（如 "name:XXX"）
    按 name/student_id 查找时自动对齐，返回 {_key → portrait} 的归一化映射。
    因为原始 students dict key 不可靠，做遍历 O(N) 构建索引（N≤1000 可接受）。
    """
    out: Dict[str, Dict[str, Any]] = {}
    raw = _agg().get("students") or {}
    for _, p in raw.items():
        if not isinstance(p, dict):
            continue
        name = p.get("name") or ""
        sid = p.get("student_id") or ""
        k = make_student_key(name, sid)
        if not k:
            continue
        p2 = dict(p)
        p2["_key"] = k
        p2.setdefault("name", name)
        p2.setdefault("student_id", sid)
        out[k] = p2
    return out


def _portrait_for(key: str) -> Optional[Dict[str, Any]]:
    sl = _sl()
    mapping = _students_portrait_map()
    # 1) 按 _key 精确
    if key in mapping:
        return mapping[key]
    # 2) 在 student_list 中按 name/student_id/_key 反向找到 _key 再查
    for s in sl:
        if key in (str(s.get("_key", "")), str(s.get("name", "")), str(s.get("student_id", ""))):
            return mapping.get(s.get("_key"))
    # 3) 遍历 mapping 兜底（名称完全相等等）
    for k, p in mapping.items():
        if key in (str(p.get("name", "")), str(p.get("student_id", "")), k):
            return p
    return None


def _aggregate_experiments(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把 portrait 里的 tasks（子任务级）按 experiment 聚合成各实验表现列表。"""
    exps: Dict[str, Dict[str, Any]] = {}
    if not isinstance(tasks, list):
        return []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        exp = str(t.get("experiment") or t.get("experiment_name") or "未命名实验")
        score = float(t.get("score") or 0)
        max_s = float(t.get("max_score") or 0)
        is_weak = bool(t.get("weak"))
        node = exps.setdefault(exp, {
            "name": exp,
            "score_sum": 0.0,
            "max_sum": 0.0,
            "weak_count": 0,
            "total": 0,
            "submitted": True,
        })
        node["score_sum"] += score
        node["max_sum"] += max_s
        node["total"] += 1
        if is_weak:
            node["weak_count"] += 1
    out = []
    for exp, v in exps.items():
        ratio = v["score_sum"] / v["max_sum"] if v["max_sum"] > 0 else 0.0
        score_100 = round(ratio * 100, 1)
        wk_rate = (v["weak_count"] / v["total"]) if v["total"] > 0 else 0.0
        out.append({
            "name": exp,
            "score": score_100,
            "weak_count": int(v["weak_count"]),
            "weakness_rate": round(wk_rate, 3),
            "submitted": bool(v["submitted"]),
        })
    out.sort(key=lambda x: x["name"])
    return out


def _format_weak_units(unit_weakness: Any) -> List[Dict[str, Any]]:
    """unit_weakness 可能是 dict/list/其他 → 统一成 [{unit, knowledge[], count}]"""
    arr: List[Dict[str, Any]] = []
    if isinstance(unit_weakness, dict):
        for u, ks in unit_weakness.items():
            if isinstance(ks, list):
                names = [str(k.get("name") or k) if isinstance(k, dict) else str(k) for k in ks if k]
            elif isinstance(ks, dict):
                names = [str(ks.get("name") or ks)]
            else:
                names = [str(ks)]
            names = [x for x in names if x]
            if names:
                arr.append({"unit": str(u), "knowledge": names, "count": len(names)})
    elif isinstance(unit_weakness, list):
        d = defaultdict(list)
        for item in unit_weakness:
            if isinstance(item, dict):
                u = str(item.get("unit") or item.get("chapter") or "未分类")
                n = str(item.get("name") or item.get("知识点") or item.get("knowledge") or "")
                if n:
                    d[u].append(n)
        for u, ns in d.items():
            if ns:
                arr.append({"unit": u, "knowledge": ns, "count": len(ns)})
    arr.sort(key=lambda x: -x["count"])
    return arr


def _format_weak_knowledge_list(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, str):
        # weak_knowledge_names 这种逗号分隔字符串
        parts = [x.strip() for x in raw.replace("、", ",").replace(";", ",").replace("，", ",").split(",") if x.strip()]
        return [{"name": p, "unit": "", "level": ""} for p in parts]
    if isinstance(raw, list):
        out = []
        for it in raw:
            if isinstance(it, dict):
                out.append({
                    "name": str(it.get("name") or it.get("知识点名称") or it.get("视频/知识点名称") or ""),
                    "unit": str(it.get("unit") or it.get("MOOC教学单元") or it.get("单元") or ""),
                    "level": str(it.get("level") or it.get("教学层次") or ""),
                })
            elif isinstance(it, str) and it.strip():
                out.append({"name": it.strip(), "unit": "", "level": ""})
        return [x for x in out if x["name"]]
    return []


def _global_top_units(sl: List[Dict[str, Any]], mapping: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """基于所有 portrait 的 unit_weakness 计算年级 Top 薄弱单元。"""
    counter: Dict[str, int] = defaultdict(int)
    for s in sl:
        p = mapping.get(s.get("_key") or "") or {}
        uw = p.get("unit_weakness")
        if isinstance(uw, dict) and uw:
            for u in uw.keys():
                counter[str(u)] += 1
    arr = [{"unit": u, "weak_student_count": c} for u, c in counter.items()]
    arr.sort(key=lambda x: -x["weak_student_count"])
    return arr


# ============================================================
# 路由：画像统计
# ============================================================
@router.get("/stats")
async def api_students_stats():
    ad = _agg()
    if not ad:
        return {"has_data": False}
    sl = _sl()
    mapping = _students_portrait_map()
    total = len(sl)
    weak_list = [s for s in sl if (s.get("weak_count") or 0) > 0]
    seg_0 = sum(1 for s in sl if (s.get("weakness_rate") or 0) == 0)
    seg_20 = sum(1 for s in sl if 0 < (s.get("weakness_rate") or 0) < 0.2)
    seg_40 = sum(1 for s in sl if 0.2 <= (s.get("weakness_rate") or 0) < 0.4)
    seg_60 = sum(1 for s in sl if 0.4 <= (s.get("weakness_rate") or 0) < 0.6)
    seg_100 = sum(1 for s in sl if (s.get("weakness_rate") or 0) >= 0.6)
    avg_rate = round(sum(s.get("weakness_rate") or 0 for s in sl) / total, 3) if total else 0
    return {
        "has_data": True,
        "total": total,
        "weak_count": len(weak_list),
        "healthy_count": total - len(weak_list),
        "avg_weakness_rate": avg_rate,
        "segmentation": {
            "rate_0": seg_0,
            "rate_0_20": seg_20,
            "rate_20_40": seg_40,
            "rate_40_60": seg_60,
            "rate_60_100": seg_100,
        },
        "top_units": _global_top_units(sl, mapping)[:10],
        "experiment_count": ad.get("experiment_count") or ad.get("experiment_coun") or 0,
        "quiz_count": ad.get("quiz_count", 0),
        "unit_count": ad.get("unit_count", 0),
        "attendance_count": ad.get("attendance_count", 0),
    }


# ============================================================
# 路由：学生列表（分页 + 搜索 + 薄弱筛选 + 排序）
# ============================================================
@router.get("")
async def api_list_students(
    keyword: str = "",
    weak_only: bool = False,
    min_weakness_rate: Optional[float] = Query(default=None, ge=0, le=1),
    sort: str = "weakness_desc",
    page: int = 1,
    page_size: int = 20,
):
    sl = _sl()
    if not sl:
        return {"has_data": False, "total": 0, "items": [], "page": page, "page_size": page_size}
    kw = (keyword or "").strip().lower()
    if kw:
        def _hit(s: Dict[str, Any]) -> bool:
            if kw in str(s.get("name", "")).lower():
                return True
            if kw in str(s.get("student_id", "")).lower():
                return True
            if kw in str(s.get("_key", "")).lower():
                return True
            # 支持搜索班级名/年份
            for key in ("class_names", "detected_years"):
                v = s.get(key)
                if isinstance(v, list):
                    for item in v:
                        if kw in str(item).lower():
                            return True
            return False
        sl = [s for s in sl if _hit(s)]
    if weak_only:
        sl = [s for s in sl if (s.get("weak_count") or 0) > 0]
    if min_weakness_rate is not None:
        sl = [s for s in sl if (s.get("weakness_rate") or 0) >= min_weakness_rate]

    if sort == "weakness_desc":
        sl.sort(key=lambda s: (-(s.get("weakness_rate") or 0), -(s.get("weak_count") or 0)))
    elif sort == "weakness_asc":
        sl.sort(key=lambda s: ((s.get("weakness_rate") or 0), (s.get("weak_count") or 0)))
    elif sort == "name":
        sl.sort(key=lambda s: (str(s.get("name", "")), str(s.get("student_id", ""))))
    elif sort == "score_desc":
        sl.sort(key=lambda s: -(float(s.get("avg_score") or s.get("final_score") or 0)))
    total = len(sl)
    page = max(1, int(page))
    page_size = max(1, min(200, int(page_size)))
    start = (page - 1) * page_size
    items = []
    for s in sl[start:start + page_size]:
        items.append({
            "_key": s.get("_key"),
            "name": s.get("name", ""),
            "student_id": s.get("student_id", ""),
            "weak_count": int(s.get("weak_count") or 0),
            "weak_subtask_count": int(s.get("weak_subtask_count") or 0),
            "weak_knowledge_count": int(s.get("weak_knowledge_count") or 0),
            "weakness_rate": round(float(s.get("weakness_rate") or 0), 3),
            "avg_score": round(float(s.get("avg_score") or 0), 1),
            "experiment_count": int(s.get("experiment_count") or 0),
        })
    return {
        "has_data": True,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    }


# ============================================================
# 路由：学生画像详情
# ============================================================
@router.get("/{key}")
async def api_student_detail(key: str):
    sl = _sl()
    # 先拿 student_list 层的数据（总览）
    list_meta = None
    for s in sl:
        if key in (str(s.get("_key", "")), str(s.get("name", "")), str(s.get("student_id", ""))):
            list_meta = s
            break
    portrait = _portrait_for(key)
    if not portrait and not list_meta:
        raise HTTPException(404, "未找到该学生画像，请确认 _key/姓名/学号正确")
    if not portrait and list_meta:
        # 有 list 但没有 portrait（极端情况），用 list_meta 做基本画像
        portrait = dict(list_meta)

    name = (portrait and portrait.get("name")) or (list_meta and list_meta.get("name")) or ""
    sid = (portrait and portrait.get("student_id")) or (list_meta and list_meta.get("student_id")) or ""
    _key = (list_meta and list_meta.get("_key")) or make_student_key(name, sid)

    # weak_rate / weak_count 优先取 portrait
    weakness_rate = float((portrait or {}).get("weak_rate") or (list_meta or {}).get("weakness_rate") or 0)
    weak_count = int((portrait or {}).get("weak_count") or (list_meta or {}).get("weak_count") or 0)
    weak_subtask_count = int((portrait or {}).get("weak_subtask_count") or (list_meta or {}).get("weak_subtask_count") or weak_count)
    weak_knowledge_count = int((portrait or {}).get("weak_knowledge_count") or (list_meta or {}).get("weak_knowledge_count") or 0)

    # experiments：从 portrait.tasks 聚合
    tasks = (portrait or {}).get("tasks") or []
    experiments = _aggregate_experiments(tasks)
    exp_cnt = max(int((list_meta or {}).get("experiment_count") or 0), len(experiments))

    # weak_units：unit_weakness 转标准化
    weak_units = _format_weak_units((portrait or {}).get("unit_weakness") or {})
    # 如果 portrait 里没有 unit_weakness，但 list 里有 weak_knowledge_names 字符串，用该字符串补齐
    if not weak_units and list_meta and list_meta.get("weak_knowledge_names"):
        arr = _format_weak_knowledge_list(list_meta["weak_knowledge_names"])
        if arr:
            # 放到"未分类"单元里
            bucket = defaultdict(list)
            for k in arr:
                bucket[k["unit"] or "未分类"].append(k["name"])
            weak_units = [{"unit": u, "knowledge": ns, "count": len(ns)} for u, ns in bucket.items()]
            weak_units.sort(key=lambda x: -x["count"])

    # weak_knowledge_list
    wk_list = _format_weak_knowledge_list((portrait or {}).get("weak_knowledge") or (list_meta or {}).get("weak_knowledge_names") or [])

    # avg_score：有 experiment 就按聚合算，否则用 list 里的 final_score 等
    if experiments:
        sc = sum(e["score"] for e in experiments) / len(experiments)
    else:
        sc = float((list_meta or {}).get("final_score") or (list_meta or {}).get("avg_score") or 0)
    avg_score = round(sc, 1)

    # 等级与建议
    if weakness_rate < 0.05 and weak_count == 0:
        level = "优秀"
    elif weakness_rate < 0.15:
        level = "良好"
    elif weakness_rate < 0.3:
        level = "中等"
    elif weakness_rate < 0.5:
        level = "薄弱"
    else:
        level = "重点预警"
    suggestions: List[str] = []
    if level == "优秀":
        suggestions.append("整体掌握扎实，可拓展进阶内容（如项目实践、算法训练），鼓励与同班薄弱学生结对辅导")
    elif level == "良好":
        suggestions.append("整体掌握较好，建议针对个别薄弱知识点做 1~2 道针对性练习即可巩固，同时回看对应 MOOC 视频章节")
    elif level == "中等":
        suggestions.append("存在一定知识漏洞，建议：(1) 回看薄弱对应 MOOC 单元视频；(2) 补做/重做薄弱子任务；(3) 完成后向老师提交一次订正结果")
    elif level == "薄弱":
        suggestions.append("薄弱点较多，建议：(1) 从薄弱子任务入手重新完成；(2) 重点回看对应单元的知识点讲解（建议做学习笔记）；(3) 完成后重新上传成绩观察薄弱率是否下降")
    else:
        suggestions.append("⚠️ 重点预警对象！建议：(1) 老师单独谈话确认学习态度与出勤；(2) 必要时与家长沟通近期学习状况")
    for wu in weak_units[:3]:
        kws_preview = "、".join(wu["knowledge"][:3]) + ("…" if wu["count"] > 3 else "")
        suggestions.append(f"重点单元「{wu['unit']}」：建议重点复习 {wu['count']} 个薄弱知识点（{kws_preview}）")

    # 成绩预测：agg 层 prediction_text 里可能有个人，暂时留空（系统级预测另外返回）
    prediction = str((portrait or {}).get("prediction") or "")

    # 读取"学生_XXX.txt"完整报告
    report_content = ""
    safe_key_candidates = [_key, make_student_key(name, sid)] + (
        [make_student_key(list_meta.get("name", ""), list_meta.get("student_id", ""))] if list_meta else []
    )
    report_filename = ""
    report_dir = Path(str(config.report_dir)).resolve()
    for ck in safe_key_candidates:
        if not ck: continue
        fn = f"学生_{ck}.txt"
        fp = (report_dir / fn).resolve()
        if not str(fp).startswith(str(report_dir)):
            continue
        if fp.exists():
            report_filename = fn
            try:
                report_content = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                logger.warning("读取学生报告[%s]失败: %s", fn, e)
            break

    return {
        "_key": _key,
        "name": name,
        "student_id": sid,
        "level": level,
        "weakness_rate": round(weakness_rate, 3),
        "weak_count": weak_count,
        "weak_subtask_count": weak_subtask_count,
        "weak_knowledge_count": weak_knowledge_count,
        "avg_score": avg_score,
        "experiment_count": exp_cnt,
        "experiments": experiments,
        "weak_units": weak_units,
        "weak_knowledge_list": wk_list,
        "top_weak_knowledge": wk_list[:15],
        "prediction": prediction,
        "suggestions": suggestions,
        "report_filename": report_filename or f"学生_{_key}.txt",
        "report_content": report_content,
    }
