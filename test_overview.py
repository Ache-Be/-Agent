# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'd:\A教育agent\server')
sys.path.insert(0, r'd:\A教育agent')

import asyncio

async def main():
    from api.v1.reports import api_report_overview
    print("Testing api_report_overview ...")
    try:
        result = await api_report_overview()
        print(f"OK! has_data={result.get('has_data')}")
        if result.get('has_data'):
            print(f"  success_count={result.get('success_count')}")
            print(f"  fail_count={result.get('fail_count')}")
            print(f"  agg keys={list(result.get('agg', {}).keys())}")
            print(f"  student_list count={len(result.get('student_list', []))}")
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(main())
