import pytest
from fastapi.testclient import TestClient

from backend.app import db, main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    main.cached_generate.cache_clear()
    return TestClient(main.app)
