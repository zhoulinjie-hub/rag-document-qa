import main

from conftest import TEST_ACCESS_CODE, build_pdf_bytes


def test_web_page_assets_and_security_headers(api_client):
    response = api_client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "智能文档问答" in response.text
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert api_client.get("/static/app.css").status_code == 200
    assert api_client.get("/static/app.js").status_code == 200
    assert api_client.get("/static/favicon.svg").status_code == 200


def test_login_and_protected_routes(api_client, login):
    assert api_client.get("/documents").status_code == 401
    assert api_client.get(
        "/documents",
        headers={"Authorization": "Bearer forged.token"},
    ).status_code == 401
    assert api_client.post(
        "/session",
        json={"access_code": "wrong-code"},
    ).status_code == 401

    headers = login()
    assert api_client.get("/documents", headers=headers).json() == {"documents": []}


def test_mocked_upload_search_answer_sources_and_owner_isolation(
    api_client,
    login,
    fake_model_client,
):
    owner_a = login()
    owner_b = login()
    upload = api_client.post(
        "/upload",
        headers=owner_a,
        files={
            "file": (
                "public-test.pdf",
                build_pdf_bytes(),
                "application/pdf",
            )
        },
    )
    assert upload.status_code == 200
    document = upload.json()
    assert document["filename"] == "public-test.pdf"
    assert document["chunk_count"] >= 1
    assert "chunks" not in document
    assert "owner_id" not in document

    documents_a = api_client.get("/documents", headers=owner_a).json()["documents"]
    documents_b = api_client.get("/documents", headers=owner_b).json()["documents"]
    assert [item["document_id"] for item in documents_a] == [document["document_id"]]
    assert documents_b == []

    search = api_client.post(
        "/search",
        headers=owner_a,
        json={
            "question": "What is the temperature threshold?",
            "top_k": 3,
            "document_id": document["document_id"],
            "min_score": 0.25,
        },
    )
    assert search.status_code == 200
    assert search.json()["results"][0]["page"] == 1

    answer = api_client.post(
        "/ask",
        headers=owner_a,
        json={
            "question": "What is the temperature threshold?",
            "top_k": 3,
            "document_id": document["document_id"],
            "min_score": 0.25,
            "use_rag": True,
        },
    )
    assert answer.status_code == 200
    assert answer.json()["mode"] == "rag"
    assert answer.json()["sources"][0]["page"] == 1
    assert "[来源1]" in answer.json()["answer"]
    assert len(fake_model_client.embeddings.calls) >= 3
    assert len(fake_model_client.chat.completions.calls) == 1

    assert api_client.post(
        "/search",
        headers=owner_b,
        json={
            "question": "threshold",
            "document_id": document["document_id"],
        },
    ).status_code == 404
    assert api_client.delete(
        f"/documents/{document['document_id']}",
        headers=owner_b,
    ).status_code == 404


def test_invalid_uploads_return_controlled_errors(api_client, login):
    headers = login()

    wrong_extension = api_client.post(
        "/upload",
        headers=headers,
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert wrong_extension.status_code == 400
    assert wrong_extension.json()["detail"] == "只支持上传 PDF 文件"

    invalid_pdf = api_client.post(
        "/upload",
        headers=headers,
        files={"file": ("broken.pdf", b"not a pdf", "application/pdf")},
    )
    assert invalid_pdf.status_code == 400
    assert invalid_pdf.json()["detail"] == "PDF 解析失败，请检查文件是否有效"


def test_no_related_content_skips_chat(api_client, login, fake_model_client):
    headers = login()
    before_calls = len(fake_model_client.chat.completions.calls)

    response = api_client.post(
        "/ask",
        headers=headers,
        json={
            "question": "What is the temperature on Mars?",
            "top_k": 3,
            "document_id": None,
            "min_score": 0.25,
            "use_rag": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["mode"] == "insufficient_context"
    assert response.json()["sources"] == []
    assert len(fake_model_client.chat.completions.calls) == before_calls


def test_internal_error_is_not_exposed(api_client, login, monkeypatch, rag_service):
    headers = login()

    def fail_search(**kwargs):
        raise RuntimeError("private-path E:/secret/token.txt upstream-details")

    monkeypatch.setattr(rag_service, "search", fail_search)
    response = api_client.post(
        "/search",
        headers=headers,
        json={"question": "trigger error"},
    )
    assert response.status_code == 502
    assert response.json()["detail"] == "向量检索失败，请稍后重试"
    assert "private-path" not in response.text
    assert "token.txt" not in response.text


def test_request_rate_limit_returns_429(api_client, login, monkeypatch):
    monkeypatch.setattr(main, "REQUESTS_PER_MINUTE", 2)
    headers = login(TEST_ACCESS_CODE)

    assert api_client.get("/documents", headers=headers).status_code == 200
    assert api_client.get("/documents", headers=headers).status_code == 200
    response = api_client.get("/documents", headers=headers)
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1
