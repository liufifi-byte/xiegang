"""测试公共 fixtures。"""
import os
import sys
from pathlib import Path

# 让 tests 能 import app
sys.path.insert(0, str(Path(__file__).parent.parent))

# 测试用环境变量（避免缺少真实凭证时报错）
os.environ.setdefault("GLM_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "http://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_DB_URL", "postgresql://test")
os.environ.setdefault("TAVILY_API_KEY", "test-key")

# V2-S1: 测试环境下跳过 runtime_context 的 DB 路径，走 in-memory
os.environ.setdefault("SKIP_DB_CONTEXT", "true")


import pytest


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)
