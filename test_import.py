# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'd:\A教育agent\server')
sys.path.insert(0, r'd:\A教育agent')

print("Importing analysis modules...")
try:
    from analysis.cross_analyzer import is_real_student, aggregate_students
    print("  cross_analyzer OK")
except Exception as e:
    import traceback; traceback.print_exc()

try:
    from analysis.touge_parser import is_real_student as t_is, load_touge_csv
    print("  touge_parser OK")
except Exception as e:
    import traceback; traceback.print_exc()

try:
    from analysis.mooc_parser import is_real_student as m_is
    print("  mooc_parser OK")
except Exception as e:
    import traceback; traceback.print_exc()

try:
    from analysis.quiz_parser import is_real_student as q_is
    print("  quiz_parser OK")
except Exception as e:
    import traceback; traceback.print_exc()

try:
    from analysis.student_analyzer import is_real_student as s_is
    print("  student_analyzer OK")
except Exception as e:
    import traceback; traceback.print_exc()

print("Importing API modules...")
try:
    from api.v1.reports import api_list_reports, api_report_overview
    print("  reports.py OK")
except Exception as e:
    import traceback; traceback.print_exc()

print("All imports done")
