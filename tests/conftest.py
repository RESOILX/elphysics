"""
conftest.py — pytest 共通設定

【役割】
  1. プロジェクトルートをインポートパスに追加し、
     `import elphysics` が解決できるようにする。
  2. 検証 API (FastAPI) のテストクライアントを fixture として提供する。
"""
import sys
from pathlib import Path

import pytest

# プロジェクトルート (このファイルの親の親) をパスに追加 → import elphysics が通る
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture(scope="session")
def api_client():
    """
    FastAPI の検証 API を、サーバーを立てずにテストできる TestClient。
    API テスト (test_dc.py) から使う。
    """
    from fastapi.testclient import TestClient
    from elphysics.api import app
    return TestClient(app)
