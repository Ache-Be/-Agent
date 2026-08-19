# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'd:\A教育agent\server')
sys.path.insert(0, r'd:\A教育agent')

import asyncio
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

print("Testing /api/reports ...")
try:
    resp = client.get("/api/reports", params={"type": "all"})
    print(f"Status: {resp.status_code}")
    if resp.status_code != 200:
        print(f"Response: {resp.text[:2000]}")
    else:
        data = resp.json()
        print(f"OK! total={data.get('total')}, items count={len(data.get('items', []))}")
except Exception as e:
    import traceback
    traceback.print_exc()
