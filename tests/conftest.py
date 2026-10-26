from __future__ import annotations

from types import SimpleNamespace

import pymupdf
import pytest
from fastapi.testclient import TestClient

import main
from rag_service import RAGService
from security import DemoSecurity


TEST_ACCESS_CODE = "pytest-access-code-at-least-32-chars"


def build_pdf_bytes(text: str = "Atlas-27 threshold is 42 C. Review within 15 minutes.") -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_textbox(
        pymupdf.Rect(72, 72, 520, 760),
        text,
        fontsize=12,
    )
    payload = document.tobytes()
    document.close()
    return payload


def fake_vector(text: str) -> list[float]:
    normalized = text.lower()
    if any(keyword in normalized for keyword in ("atlas", "threshold", "42", "temperature")):
        return [1.0, 0.0, 0.0]
    if any(keyword in normalized for keyword in ("mars", "unrelated")):
        return [0.0, 1.0, 0.0]
    return [0.6, 0.4, 0.0]


class FakeEmbeddings:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, *, model: str, input: list[str]):
        self.calls.append({"model": model, "input": list(input)})
        data = [
            SimpleNamespace(index=index, embedding=fake_vector(text))
            for index, text in enumerate(input)
        ]
        return SimpleNamespace(data=data)


class FakeChatCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(
            content="Atlas-27 的温度预警阈值是 42 摄氏度。[来源1]"
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeModelClient:
    def __init__(self) -> None:
        self.embeddings = FakeEmbeddings()
        self.chat = SimpleNamespace(completions=FakeChatCompletions())


@pytest.fixture
def fake_model_client() -> FakeModelClient:
    return FakeModelClient()


@pytest.fixture
def rag_service(tmp_path, fake_model_client) -> RAGService:
    service = RAGService(
        api_key=None,
        base_url="https://example.invalid",
        embedding_model="mock-embedding",
        chat_model="mock-chat",
        index_path=tmp_path / "rag_index.json",
    )
    service._client = fake_model_client
    return service


@pytest.fixture
def api_client(monkeypatch, rag_service):
    monkeypatch.setattr(main, "rag_service", rag_service)
    monkeypatch.setattr(
        main,
        "demo_security",
        DemoSecurity(TEST_ACCESS_CODE, session_ttl_minutes=60),
    )
    with TestClient(main.app) as client:
        yield client


@pytest.fixture
def login(api_client):
    def _login(access_code: str = TEST_ACCESS_CODE) -> dict[str, str]:
        response = api_client.post("/session", json={"access_code": access_code})
        assert response.status_code == 200
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _login
