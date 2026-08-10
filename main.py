"""
学生错题-知识点智能分析体 v0.2

用法：
  # 1. 首先生成知识点库
  python main.py build-knowledge

  # 2a. 分析单个头歌实验
  python main.py touge --file "data/touge/xxx.csv"

  # 2b. 分析单个MOOC班级
  python main.py mooc --file "data/mooc/xxx.csv"

  # 2c. 批量分析整个目录
  python main.py touge --dir "tougeall/tougeall/Java程序设计（2023秋季）"
  python main.py mooc --dir "MOOC无乱码/无乱码"

  # 3. 生成汇总报告
  python main.py summary
"""

import argparse
import sys
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from analysis.touge_parser import scan_touge_directory
from analysis.mooc_parser import scan_mooc_directory
from analysis.knowledge_builder import (
    build_knowledge_from_chapter_map,
    load_knowledge_base,
)
from analysis.analyzer import (
    analyze_touge_file,
    analyze_touge_directory,
    analyze_mooc_file,
    analyze_mooc_directory,
)
from analysis.quiz_parser import analyze_quiz_directory
from analysis.reporter import (
    generate_touge_report,
    generate_mooc_report,
    generate_summary_report,
)

# 默认路径
CHAPTER_MAP = ROOT / "word+excel" / "智慧慕课-慕课章节对应表.xlsx"
KNOWLEDGE_CSV = ROOT / "data" / "knowledge" / "knowledge_base.csv"
# 头歌实验数据目录（2026.7 新数据结构：智慧树头歌学生学习数据/头歌实验_2026.7.21/课程/）
TOUGE_ROOT = ROOT / "头歌" / "tougeall" / "智慧树头歌学生学习数据" / "头歌实验_2026.7.21"
QUIZ_ROOT = ROOT / "头歌" / "tougeall" / "智慧树头歌学生学习数据" / "随堂测验"
MOOC_ROOT = ROOT / "MOOC"
OUTPUT_DIR = ROOT / "output"


def cmd_build_knowledge(args):
    """从章节对应表生成知识点库"""
    print(f"正在从章节对应表生成知识点库...")
    print(f"  源文件：{CHAPTER_MAP}")
    print(f"  输出：{KNOWLEDGE_CSV}")

    KNOWLEDGE_CSV.parent.mkdir(parents=True, exist_ok=True)
    records = build_knowledge_from_chapter_map(
        str(CHAPTER_MAP), str(KNOWLEDGE_CSV)
    )
    print(f"  完成！共生成了 {len(records)} 条知识点记录")


def cmd_analyze_touge(args):
    """分析头歌实验数据"""
    kb = load_knowledge_base(str(KNOWLEDGE_CSV)) if KNOWLEDGE_CSV.exists() else None

    if args.file:
        results = [analyze_touge_file(args.file, kb)]
    elif args.dir:
        results = analyze_touge_directory(args.dir, kb)
    else:
        results = analyze_touge_directory(str(TOUGE_ROOT), kb)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 输出每个实验的报告
    for r in results:
        if "error" in r:
            print(f"  [错误] {r.get('file_path', '')}：{r['error']}")
            continue

        report = generate_touge_report(r)
        print(report)
        print("=" * 64)

        # 保存到文件
        safe_name = r.get("experiment_name", "unknown")
        for c in r'\/:*?"<>|':
            safe_name = safe_name.replace(c, "_")
        report_path = OUTPUT_DIR / f"touge_{safe_name}.txt"
        report_path.write_text(report, encoding="utf-8")
        print(f"  报告已保存：{report_path}\n")


def cmd_analyze_mooc(args):
    """分析MOOC班级数据"""
    kb = load_knowledge_base(str(KNOWLEDGE_CSV)) if KNOWLEDGE_CSV.exists() else None

    if args.file:
        results = [analyze_mooc_file(args.file, kb)]
    elif args.dir:
        results = analyze_mooc_directory(args.dir, kb)
    else:
        results = analyze_mooc_directory(str(MOOC_ROOT), kb)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for r in results:
        if "error" in r:
            print(f"  [错误] {r.get('file_path', '')}：{r['error']}")
            continue

        report = generate_mooc_report(r)
        print(report)
        print("=" * 64)

        safe_name = r.get("classroom_name", "unknown")
        for c in r'\/:*?"<>|':
            safe_name = safe_name.replace(c, "_")
        report_path = OUTPUT_DIR / f"mooc_{safe_name}.txt"
        report_path.write_text(report, encoding="utf-8")
        print(f"  报告已保存：{report_path}\n")


def cmd_analyze_quiz(args):
    """分析随堂测验数据"""
    target = args.dir or str(QUIZ_ROOT)
    results = analyze_quiz_directory(target)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    for r in results:
        if "error" in r:
            print(f"  [错误] {r.get('file_path', '')}：{r['error']}")
            continue
        ok += 1
        print(f"  [{r['class_name'] or r.get('class_dir','')}] 项目{r['project_id']} {r['chapter_name']} "
              f"| 学生{r['student_count']} | 平均正确率{r['avg_accuracy']*100:.0f}% | 薄弱{r['weak_count']}人")

    print(f"\n共分析 {ok}/{len(results)} 个随堂测验")

    # 汇总保存
    summary_lines = ["随堂测验分析汇总", "=" * 40]
    for r in results:
        if "error" in r:
            continue
        summary_lines.append(
            f"[{r['class_name'] or r.get('class_dir','')}] 项目{r['project_id']} {r['chapter_name']}: "
            f"{r['student_count']}人 平均正确率{r['avg_accuracy']*100:.0f}% 薄弱{r['weak_count']}人")
    summary_path = OUTPUT_DIR / "随堂测验汇总.txt"
    summary_path.write_text("\n".join(summary_lines), encoding="utf-8")
    print(f"  汇总已保存：{summary_path}")


def cmd_summary(args):
    """生成汇总报告"""
    kb = load_knowledge_base(str(KNOWLEDGE_CSV)) if KNOWLEDGE_CSV.exists() else None

    print("正在批量分析头歌数据...")
    touge_results = analyze_touge_directory(str(TOUGE_ROOT), kb)
    print(f"  分析了 {len(touge_results)} 个实验")

    print("正在批量分析MOOC数据...")
    mooc_results = analyze_mooc_directory(str(MOOC_ROOT), kb)
    print(f"  分析了 {len(mooc_results)} 个班级")

    report = generate_summary_report(touge_results, mooc_results)
    print(report)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUTPUT_DIR / "summary_report.txt"
    report_path.write_text(report, encoding="utf-8")
    print(f"  汇总报告已保存：{report_path}")


def main():
    parser = argparse.ArgumentParser(description="学生错题-知识点智能分析体 v0.2")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # build-knowledge
    p = subparsers.add_parser("build-knowledge", help="从章节对应表生成知识点库")

    # touge
    p = subparsers.add_parser("touge", help="分析头歌实验数据")
    p.add_argument("--file", help="指定单个 CSV 文件")
    p.add_argument("--dir", help="指定目录（批量分析所有CSV）")

    # mooc
    p = subparsers.add_parser("mooc", help="分析MOOC班级数据")
    p.add_argument("--file", help="指定单个 CSV 文件")
    p.add_argument("--dir", help="指定目录（批量分析所有CSV）")

    # quiz
    p = subparsers.add_parser("quiz", help="分析随堂测验 xlsx 数据")
    p.add_argument("--dir", help="指定目录（默认扫描全部随堂测验）")

    # summary
    subparsers.add_parser("summary", help="生成全量汇总报告")

    args = parser.parse_args()

    if args.command == "build-knowledge":
        cmd_build_knowledge(args)
    elif args.command == "touge":
        cmd_analyze_touge(args)
    elif args.command == "mooc":
        cmd_analyze_mooc(args)
    elif args.command == "quiz":
        cmd_analyze_quiz(args)
    elif args.command == "summary":
        cmd_summary(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
