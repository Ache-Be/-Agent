"""
跨实验聚合分析。

将多个实验（头歌 CSV）的结果合并，按学生维度进行全量分析：
1. 收集该学生在所有实验中的所有子任务得分
2. 正确率低于薄弱阈值的标记为"薄弱"
3. 关联到知识点
4. 生成按学生的综合报告
"""

from collections import defaultdict
from typing import List, Dict, Optional, Set, Tuple
from datetime import datetime
import re

from analysis.subtask_mapper import map_subtask_to_knowledge
from analysis.config import load_config

# 正确率低于此阈值视为薄弱/易错（可在 web/config/settings.json 中调整）
WEAK_THRESHOLD = load_config()["weak_threshold"]


# 年份正则：抓 2020~2099 之间的 4 位数；再兜底 19xx / 2 位简写年份（19~99 → 20xx）
_YEAR_RE = re.compile(r"(?<!\d)(20[0-9]{2})(?!\d)")
_SHORT_YEAR_RE = re.compile(r"(?<!\d)([1-9][0-9])(?!\d)")

# 班级正则：抓"软件/计算机/机电/Java/苏理工/人工智能/大数据/软工/计科"等常见系 + 数字+班；兜底 xxxx班
_CLASS_TOKEN_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z]{1,8}?\s*\d+\s*班")
# 直接包含"班"字的简单词
_CLASS_HINT_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z0-9]{1,12}?班")


def _extract_years(*texts: str) -> Set[str]:
    """从任意组字符串中提取出现的 4 位年份集合，兜底 2 位年份按 20xx 补全"""
    out: Set[str] = set()
    for t in texts:
        if not t:
            continue
        # 特殊处理：如果文本是长学号（10位以上数字），前两位通常是年份简写
        if t.isdigit() and len(t) >= 10:
            yy_str = t[:2]
            try:
                yy = int(yy_str)
                if 10 <= yy <= 30: # 假设是 2010~2030 之间的入学者
                    out.add(f"20{yy:02d}")
            except Exception:
                pass

        for m in _YEAR_RE.findall(t):
            out.add(m)
        if not out:
            for m in _SHORT_YEAR_RE.findall(t):
                try:
                    yy = int(m)
                except Exception:
                    continue
                if 19 <= yy <= 99:
                    out.add(f"20{yy:02d}")
    return out


def _extract_class_names(*texts: str) -> Set[str]:
    """从任意组字符串中提取疑似班级名（出现"班"字优先，其次常见专业+数字班模式）"""
    out: Set[str] = set()
    for t in texts:
        if not t:
            continue
        # 特殊处理：如果文本看起来像长学号（8位以上数字），尝试从中提取班级信息
        # 例如 232219605120 -> 23级某专业
        if t.isdigit() and len(t) >= 10:
            year = t[:2]
            major = t[2:8]
            # 这里只是粗略推断，暂不作为强班级名，仅做辅助
            # out.add(f"{year}级{major}") 
            pass

        for m in _CLASS_TOKEN_RE.findall(t):
            out.add(m.strip())
        if not out:
            for m in _CLASS_HINT_RE.findall(t):
                out.add(m.strip())
    return out


def _make_split_key(base_sid: str, class_names: Set[str]) -> str:
    """
    同学生（同 base_sid）但来自不同班级 → 聚合时拆成两条，避免跨班/跨年重名成绩相互污染。
    优先级：
      1. 班级集合不为空 → base_sid + '#C#' + 排序后第一班名
      2. 否则直接用 base_sid（保持历史聚合键不变，不破坏旧报告 safe_key）
    """
    if not class_names:
        return base_sid
    first_class = sorted(class_names)[0]
    return f"{base_sid}#C#{first_class}"


# 中文数字 → 整数
_CN_NUMS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _unit_sort_key(unit: str) -> tuple:
    """按教学单元编号排序（第一单元→1, 第二单元→2…），未分类排最后"""
    m = re.match(r'第(.+)单元', unit)
    if m:
        cn = m.group(1)
        total = 0
        for c in cn:
            total = total * 10 + _CN_NUMS.get(c, 0)
        return (0, total)
    if unit.startswith("阶段"):
        return (1, unit)
    return (2, unit)

# 需要排除的教师/管理员姓名（可在 web/config/settings.json 的 exclude_names 中调整）
EXCLUDED_NAMES = set(load_config()["exclude_names"])


def is_real_student(name: str) -> bool:
    """
    判断是否为真实学生姓名（过滤测试账号、教师、实验标题等）。
    
    过滤规则：
    1. 姓名为空 → 排除
    2. 姓名在排除名单中 → 排除（教师名如"卢冶"）
    3. 姓名纯字母/数字/下划线（无汉字）→ 排除（测试账号如 pue9q3pyi）
    4. 姓名过长（通常 > 6 个字符）且包含"实验/练习/项目/202"等关键字 → 排除（系统导出时的冗余行）
    """
    if not name or not name.strip():
        return False
    name = name.strip()
    if name in EXCLUDED_NAMES:
        return False
    # 如果姓名中不包含任何中文字符，视为非真实学生
    if not re.search(r'[\u4e00-\u9fff]', name):
        return False
    
    # 过滤疑似实验标题的长字符串
    if len(name) > 6:
        bad_keywords = ["实验", "练习", "项目", "测试", "作业", "202", "汇总", "分析"]
        if any(k in name for k in bad_keywords):
            return False
            
    return True


def aggregate_students(
    experiment_results: List[Dict],
    knowledge_base: List[Dict],
    quiz_results: Optional[List[Dict]] = None,
) -> Dict:
    """
    将所有实验结果按学生聚合分析。

    参数:
        experiment_results: 头歌实验分析结果列表
        knowledge_base: 知识点库
        quiz_results: 随堂测验分析结果列表（可空），按学号合并章节成绩

    返回:
        {
            "students": {student_id: student_agg_data, ...},
            "student_list": [{id, name, total_weak, ...}, ...],
            "knowledge_error_rate": {knowledge_name: {count, rate, ...}},
            "experiment_count": N,
            "quiz_count": M,
            "total_students": N,
        }
    """
    # 按学生聚合所有子任务数据
    # student_map: split_key(=sid 或 sid#C#班名) -> {name, sid, tasks, quizzes, experiments, class_names, detected_years}
    student_map = defaultdict(lambda: {
        "name": "",
        "student_id": "",
        "raw_student_id": "",
        "tasks": [],
        "quizzes": [],
        "experiments": set(),
        "class_names": set(),
        "detected_years": set(),
        "base_sid": "",
    })

    # 姓名 → 真实学号（来自随堂测验，学号完整）。用于把实验 CSV 中的脱敏学号关联到真实学号
    name_to_sid = {}
    for quiz in quiz_results or []:
        if "error" in quiz:
            continue
        for qs in quiz.get("students", []):
            qname = (qs.get("name") or "").strip()
            qsid = (qs.get("student_id") or "").strip()
            if qname and qsid and is_real_student(qname) and "*" not in qsid:
                name_to_sid.setdefault(qname, qsid)

    all_weak_subtasks = []  # 用于汇总统计

    for exp_result in experiment_results:
        exp_name = exp_result.get("experiment_name", "")
        file_name = exp_result.get("file_path") or exp_result.get("filename") or ""
        exp_class_names = _extract_class_names(exp_name, file_name, exp_result.get("class_name", ""))
        exp_years = _extract_years(exp_name, file_name, str(exp_result.get("uploaded_name", "")))
        students = exp_result.get("students", [])
        task_stats = exp_result.get("task_stats", {})

        for s in students:
            # 过滤非真实学生（测试账号、教师等）
            sname = (s.get("name") or "").strip()
            if not is_real_student(sname):
                continue

            sid = (s.get("student_id") or "").strip()
            raw_sid = sid
            if not sid or "*" in sid:
                # 学号缺失或脱敏（如 24********16）：优先按姓名关联到随堂测验的真实学号；
                # 找不到同名学生时，用姓名作为聚合键（避免脱敏学号碰撞把不同学生合并）
                sid = name_to_sid.get(sname) or f"name:{sname}"

            # 计算本次学生对应的班级集合：实验级 + 学生自身 class_name
            this_class_names: Set[str] = set(exp_class_names)
            s_class = s.get("class_name")
            if isinstance(s_class, str) and s_class.strip():
                for c in _extract_class_names(s_class):
                    this_class_names.add(c)
            this_years: Set[str] = set(exp_years)
            for y in _extract_years(s_class or "", s.get("group_name", "") or ""):
                this_years.add(y)

            # 同一个 base_sid + 不同班级 → 拆成两条聚合（跨班/跨年重名隔离）
            base_sid = sid
            split_key = _make_split_key(base_sid, this_class_names)

            student = student_map[split_key]
            student["name"] = sname
            student["student_id"] = sid
            student["raw_student_id"] = raw_sid
            student["base_sid"] = base_sid
            student["experiments"].add(exp_name)
            for c in this_class_names:
                student["class_names"].add(c)
            for y in this_years:
                student["detected_years"].add(y)

            for tid, ts in sorted(task_stats.items(), key=lambda x: int(x[0].replace("task", ""))):
                max_score = ts.get("max_score", 0)
                if max_score <= 0:
                    continue

                subtask_name = ts.get("task_name", "")
                student_task = s.get("tasks", {}).get(tid, {})
                score = student_task.get("score", 0) or 0
                rate = score / max_score

                task_entry = {
                    "experiment": exp_name,
                    "task_id": tid,
                    "subtask_name": subtask_name,
                    "score": score,
                    "max_score": max_score,
                    "rate": round(rate, 2),
                }

                # 标记薄弱子任务，并关联知识点
                if rate < WEAK_THRESHOLD:
                    knowledge = map_subtask_to_knowledge(subtask_name, knowledge_base)
                    task_entry["weak"] = True
                    task_entry["knowledge"] = knowledge
                    all_weak_subtasks.append({
                        "student_id": sid,
                        "student_name": student["name"],
                        "experiment": exp_name,
                        "subtask_name": subtask_name,
                        "rate": rate,
                        "knowledge": [k.get("视频/知识点名称", "") for k in knowledge],
                    })
                else:
                    task_entry["weak"] = False
                    task_entry["knowledge"] = []

                student["tasks"].append(task_entry)

    # ---- 合并随堂测验章节成绩（按学号+班级拆分键） ----
    quiz_count = 0
    for quiz in quiz_results or []:
        if "error" in quiz:
            continue
        quiz_count += 1
        chapter = quiz.get("chapter_name", "") or quiz.get("filename", "")
        project_id = quiz.get("project_id", "")
        quiz_class_name = quiz.get("class_name", "") or ""
        quiz_filename = quiz.get("uploaded_name") or quiz.get("filename") or ""
        quiz_classes = _extract_class_names(quiz_class_name, quiz_filename, chapter)
        quiz_years = _extract_years(quiz_class_name, quiz_filename, chapter)
        for qs in quiz.get("students", []):
            sid = qs.get("student_id", "")
            if not sid:
                continue
            # 随堂测验中也可能包含非真实学生
            if not is_real_student(qs.get("name", "")):
                continue
            # 跳过未参与本次测验的学生（参与答题数为0），未参与≠薄弱
            if not (qs.get("answer_count") or 0) > 0:
                continue
            q_class = qs.get("class_name", "") or ""
            this_class_names: Set[str] = set(quiz_classes)
            for c in _extract_class_names(q_class):
                this_class_names.add(c)
            this_years: Set[str] = set(quiz_years)
            for y in _extract_years(q_class):
                this_years.add(y)
            base_sid = sid
            split_key = _make_split_key(base_sid, this_class_names)
            student = student_map[split_key]
            student["name"] = qs.get("name", student["name"])
            student["student_id"] = sid
            student["raw_student_id"] = qs.get("student_id", "")
            student["base_sid"] = base_sid
            for c in this_class_names:
                student["class_names"].add(c)
            for y in this_years:
                student["detected_years"].add(y)
            student["quizzes"].append({
                "project_id": project_id,
                "chapter": chapter,
                "class_name": q_class or (sorted(this_class_names)[0] if this_class_names else ""),
                "score": qs.get("score") or 0,
                "total_score": quiz.get("total_score") or 0,
                "accuracy": qs.get("accuracy") or 0,
            })

    # 构建返回结构
    student_list = []
    students_output = {}
    # 用于整体 AI 上下文展示清单：全部班级/年份/实验名
    _all_classes: Set[str] = set()
    _all_years: Set[str] = set()
    _all_experiments: Set[str] = set()

    for split_key, data in student_map.items():
        # 对外展示的学号：优先真实学号；纯姓名键（name:xxx）时展示原始（可能脱敏）学号
        base_sid = data.get("base_sid") or split_key
        display_sid = data.get("raw_student_id") or ("" if str(base_sid).startswith("name:") else base_sid)
        # 班级名也可作为 display 后缀，但不写入 student_id 本身（避免下游当作学号解析）
        class_names: Set[str] = data.get("class_names") or set()
        detected_years: Set[str] = data.get("detected_years") or set()
        for c in class_names:
            _all_classes.add(c)
        for y in detected_years:
            _all_years.add(y)
        for exp in (data.get("experiments") or set()):
            _all_experiments.add(exp)
        weak_count = sum(1 for t in data["tasks"] if t["weak"])
        total_tasks = len(data["tasks"])
        weak_rate = weak_count / total_tasks if total_tasks > 0 else 0

        # ---- 随堂测验章节统计 ----
        quiz_chapters = data["quizzes"]
        quiz_weak = [q for q in quiz_chapters if q["accuracy"] < WEAK_THRESHOLD]
        quiz_accs = [q["accuracy"] for q in quiz_chapters if q["accuracy"] > 0]
        quiz_avg = round(sum(quiz_accs) / len(quiz_accs), 2) if quiz_accs else 0
        quiz_weak_names = [f"{q['project_id']}-{q['chapter']}" for q in quiz_weak]

        # 聚合薄弱知识点
        weak_knowledge = []
        seen_knowledge = set()
        for t in data["tasks"]:
            if t["weak"]:
                for k in t.get("knowledge", []):
                    kn = k.get("视频/知识点名称", "")
                    unit = k.get("MOOC教学单元", "")
                    key = f"{unit}|{kn}"
                    if key not in seen_knowledge:
                        seen_knowledge.add(key)
                        weak_knowledge.append({
                            "name": kn,
                            "unit": unit,
                            "related_subtask": t["subtask_name"],
                            "related_experiment": t["experiment"],
                        })

        # 按单元分组
        unit_weakness = defaultdict(list)
        for kw in weak_knowledge:
            unit_weakness[kw["unit"] or "未分类"].append(kw)

        student_classes_sorted = sorted(class_names)
        student_years_sorted = sorted(detected_years)
        student_info = {
            "name": data["name"],
            "student_id": display_sid,
            "class_names": student_classes_sorted,
            "detected_years": student_years_sorted,
            "experiment_count": len(data["experiments"]),
            "total_subtasks": total_tasks,
            "weak_count": weak_count,
            "weak_rate": round(weak_rate, 2),
            "weak_knowledge": weak_knowledge,
            "unit_weakness": dict(unit_weakness),
            "weak_knowledge_count": len(weak_knowledge),
            "tasks": data["tasks"],
            "quiz_chapters": quiz_chapters,
            "quiz_count": len(quiz_chapters),
            "quiz_weak_count": len(quiz_weak),
            "quiz_weak_chapters": quiz_weak_names,
            "quiz_avg_accuracy": quiz_avg,
        }
        students_output[split_key] = student_info
        # 列表也带班级/年份（方便搜索"某某班"时直接命中）
        student_list.append({
            "name": data["name"],
            "student_id": display_sid,
            "class_names": student_classes_sorted,
            "detected_years": student_years_sorted,
            "experiment_count": len(data["experiments"]),
            "total_subtasks": total_tasks,
            "weak_count": weak_count,
            "weak_rate": round(weak_rate, 2),
            "weak_knowledge_count": len(weak_knowledge),
            "weak_knowledge_names": "、".join(sorted(k["name"] for k in weak_knowledge)) if weak_knowledge else "",
            "quiz_count": len(quiz_chapters),
            "quiz_weak_count": len(quiz_weak),
            "quiz_weak_chapters": quiz_weak_names,
            "quiz_avg_accuracy": quiz_avg,
        })

    # 按薄弱率排序
    student_list.sort(key=lambda x: -x["weak_rate"])

    # 统计知识点易错率（按去重学生数，避免同一学生在多个实验重复计）
    total_students = len(student_map)
    knowledge_student_set = defaultdict(set)  # 知识点 → 有薄弱的学生ID集合
    knowledge_unit_map = {}

    for sid_info in all_weak_subtasks:
        sid = sid_info["student_id"]
        for kn in sid_info["knowledge"]:
            if kn:
                knowledge_student_set[kn].add(sid)

    # 从学生数据中补全知识点单元信息
    knowledge_error_rate = {}
    for kn, student_set in knowledge_student_set.items():
        unit = ""
        for sid, si in students_output.items():
            for kw in si["weak_knowledge"]:
                if kw["name"] == kn:
                    unit = kw["unit"]
                    break
            if unit:
                break
        knowledge_error_rate[kn] = {
            "error_count": len(student_set),
            "error_rate": round(len(student_set) / total_students, 2) if total_students > 0 else 0,
            "unit": unit,
        }

    top_error = sorted(knowledge_error_rate.items(), key=lambda x: -x[1]["error_rate"])

    return {
        "students": students_output,
        "student_list": student_list,
        "knowledge_error_rate": knowledge_error_rate,
        "top_error_knowledge": top_error[:30],
        "experiment_count": len(experiment_results),
        "quiz_count": quiz_count,
        "total_students": len(student_map),
        # 新增：整体班级/年份/实验名清单，供 AI 上下文直接枚举匹配用户问题"某某年某班"
        "all_class_names": sorted(_all_classes),
        "all_detected_years": sorted(_all_years),
        "all_experiment_names": sorted(_all_experiments),
    }


def generate_cross_student_report(student_info: Dict) -> str:
    """生成单个学生的跨实验综合报告"""
    lines = []
    lines.append("=" * 64)
    lines.append(f"  学生个人综合报告")
    lines.append("=" * 64)
    lines.append(f"姓名：{student_info['name']}")
    lines.append(f"学号：{student_info['student_id']}")
    lines.append(f"涉及实验：{student_info['experiment_count']} 个")
    lines.append(f"总子任务数：{student_info['total_subtasks']}")
    lines.append(f"薄弱子任务：{student_info['weak_count']} 个（{student_info['weak_rate']*100:.0f}%）")
    lines.append(f"关联薄弱知识点：{student_info['weak_knowledge_count']} 个")
    lines.append("")

    # 薄弱子任务详情（按实验分组）
    if student_info["tasks"]:
        weak_tasks = [t for t in student_info["tasks"] if t["weak"]]
        if weak_tasks:
            lines.append("-" * 64)
            lines.append(f"【薄弱子任务（正确率 < {WEAK_THRESHOLD*100:.0f}%）】")
            lines.append("-" * 64)
            current_exp = ""
            for t in weak_tasks:
                if t["experiment"] != current_exp:
                    current_exp = t["experiment"]
                    lines.append(f"\n  [{current_exp}]")
                kn_names = [k.get("视频/知识点名称", "") for k in t.get("knowledge", [])]
                kn_str = " → ".join(kn_names[:3]) if kn_names else "（未匹配到知识点）"
                lines.append(
                    f"    {t['subtask_name']:<30} "
                    f"得分 {t['score']:.0f}/{t['max_score']:.0f} "
                    f"({t['rate']*100:.0f}%)"
                )
                lines.append(f"      {kn_str}")
            lines.append("")

    # 薄弱知识点汇总（按单元分组）
    if student_info["unit_weakness"]:
        lines.append("-" * 64)
        lines.append("【薄弱知识点汇总】")
        lines.append("-" * 64)
        sorted_units = sorted(student_info["unit_weakness"].keys(), key=_unit_sort_key)
        for unit in sorted_units:
            kws = student_info["unit_weakness"][unit]
            lines.append(f"  [{unit}]")
            for kw in kws:
                lines.append(f"    · {kw['name']}（来自：{kw['related_subtask']}）")
        lines.append("")

    # 随堂测验章节成绩
    quiz_chapters = student_info.get("quiz_chapters", [])
    if quiz_chapters:
        lines.append("-" * 64)
        lines.append(f"【随堂测验章节成绩】共 {len(quiz_chapters)} 个章节，"
                     f"平均正确率 {student_info.get('quiz_avg_accuracy', 0)*100:.0f}%")
        lines.append("-" * 64)
        for q in quiz_chapters:
            acc = q["accuracy"]
            flag = "  ⚠薄弱" if acc < WEAK_THRESHOLD else ""
            lines.append(
                f"  [项目{q['project_id']} {q['chapter']}] "
                f"得分 {q['score']:.0f}/{q['total_score']:.0f} "
                f"正确率 {acc*100:.0f}%{flag}"
            )
        lines.append("")

    # 学习建议
    if student_info["weak_knowledge_count"] > 0:
        lines.append("-" * 64)
        lines.append("【学习建议】")
        lines.append("-" * 64)
        units = sorted(student_info["unit_weakness"].keys(), key=_unit_sort_key)
        if units:
            lines.append(f"  需要加强的单元：{'、'.join(units)}")
        weak_pct = student_info["weak_rate"] * 100
        if weak_pct >= 50:
            lines.append("  整体掌握情况较差，建议系统复习")
        elif weak_pct >= 30:
            lines.append("  部分知识点掌握不牢，建议针对性复习")
        else:
            lines.append("  个别知识点需要巩固")

    quiz_weak = student_info.get("quiz_weak_chapters", [])
    if quiz_weak:
        lines.append(f"  随堂测验薄弱章节：{'、'.join(quiz_weak)}")

    lines.append("")
    lines.append("=" * 64)
    lines.append(f"  报告生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 64)
    return "\n".join(lines)


def generate_cross_summary(agg_result: Dict) -> str:
    """生成跨实验汇总报告"""
    lines = []
    lines.append("=" * 64)
    lines.append(f"  学生综合报告 - 全体汇总")
    lines.append("=" * 64)
    lines.append(f"实验数：{agg_result['experiment_count']}")
    lines.append(f"学生数：{agg_result['total_students']}")
    lines.append(f"有薄弱项的学生：{len([s for s in agg_result['student_list'] if s['weak_count'] > 0])} 人")
    lines.append("")

    # 易错知识点排行
    top_error = agg_result.get("top_error_knowledge", [])
    if top_error:
        lines.append("-" * 64)
        lines.append("【知识点易错率排行】")
        lines.append("-" * 64)
        header = f"{'知识点名称':<28} {'所属单元':<20} {'易错人数':<10} {'易错率':<8}"
        lines.append(header)
        lines.append("-" * 64)
        for name, info in top_error[:15]:
            unit = info["unit"][:18] if info["unit"] else "-"
            lines.append(f"{name[:26]:<28} {unit:<20} {info['error_count']:<10} {info['error_rate']*100:.0f}%")
        lines.append("")

    # 学生列表
    lines.append("-" * 64)
    lines.append("【学生薄弱概况】")
    lines.append("-" * 64)
    header = f"{'姓名':<12} {'学号':<16} {'实验数':<8} {'薄弱子任务':<12} {'薄弱知识点':<12} {'薄弱率':<8}"
    lines.append(header)
    lines.append("-" * 64)
    for s in agg_result["student_list"]:
        lines.append(
            f"{s['name']:<12} {s['student_id']:<16} {s['experiment_count']:<8} "
            f"{s['weak_count']:<12} {s['weak_knowledge_count']:<12} {s['weak_rate']*100:.0f}%"
        )

    lines.append("")
    lines.append("=" * 64)
    lines.append(f"  报告生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 64)
    return "\n".join(lines)
