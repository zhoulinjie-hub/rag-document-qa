import json

import pytest

from rag_service import (
    DocumentNotFoundError,
    PDFProcessingError,
    RAGService,
    cosine_similarity,
    split_text,
)

from conftest import build_pdf_bytes


def test_split_text_preserves_overlap():
    assert split_text("abcdef", chunk_size=4, overlap=2) == ["abcd", "cdef"]


@pytest.mark.parametrize(
    ("chunk_size", "overlap"),
    [(0, 0), (4, -1), (4, 4), (4, 5)],
)
def test_split_text_rejects_invalid_parameters(chunk_size, overlap):
    with pytest.raises(ValueError):
        split_text("abcdef", chunk_size=chunk_size, overlap=overlap)


def test_cosine_similarity_handles_regular_and_zero_vectors():
    assert cosine_similarity([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine_similarity([0, 0], [1, 0]) == 0.0


def test_index_search_answer_and_persistence(rag_service, fake_model_client):
    pdf_bytes = build_pdf_bytes()
    upload = rag_service.index_pdf(
        pdf_bytes,
        "public-test.pdf",
        owner_id="owner-a",
        chunk_size=120,
        overlap=20,
    )

    assert upload["chunk_count"] >= 1
    assert upload["embedding_dimension"] == 3
    assert "chunks" not in upload
    assert "owner_id" not in upload

    results = rag_service.search(
        "What is the temperature threshold?",
        owner_id="owner-a",
        document_id=upload["document_id"],
        top_k=3,
        min_score=0.25,
    )
    assert results
    assert results[0]["page"] == 1
    assert "42 C" in results[0]["text"]

    answer = rag_service.answer(
        "What is the temperature threshold?",
        owner_id="owner-a",
        document_id=upload["document_id"],
        top_k=3,
        min_score=0.25,
    )
    assert answer["mode"] == "rag"
    assert answer["sources"]
    assert "[来源1]" in answer["answer"]
    assert len(fake_model_client.chat.completions.calls) == 1

    reloaded = RAGService(
        api_key=None,
        base_url="https://example.invalid",
        embedding_model="mock-embedding",
        chat_model="mock-chat",
        index_path=rag_service.index_path,
    )
    assert len(reloaded.list_documents("owner-a")) == 1
    assert reloaded.list_documents("owner-b") == []


def test_same_pdf_isolated_between_owners(rag_service):
    pdf_bytes = build_pdf_bytes()
    first = rag_service.index_pdf(pdf_bytes, "same.pdf", owner_id="owner-a")
    second = rag_service.index_pdf(pdf_bytes, "same.pdf", owner_id="owner-b")

    assert first["document_id"] != second["document_id"]
    assert len(rag_service.list_documents("owner-a")) == 1
    assert len(rag_service.list_documents("owner-b")) == 1

    with pytest.raises(DocumentNotFoundError):
        rag_service.search(
            "threshold",
            owner_id="owner-b",
            document_id=first["document_id"],
        )
    with pytest.raises(DocumentNotFoundError):
        rag_service.delete_document(first["document_id"], owner_id="owner-b")


def test_no_sources_returns_insufficient_context_without_chat(rag_service, fake_model_client):
    answer = rag_service.answer("unrelated question", owner_id="empty-owner")

    assert answer["mode"] == "insufficient_context"
    assert answer["sources"] == []
    assert answer["model"] is None
    assert fake_model_client.chat.completions.calls == []


def test_invalid_and_image_only_pdf_are_rejected(rag_service):
    with pytest.raises(PDFProcessingError):
        rag_service.index_pdf(b"not a pdf", "bad.pdf", owner_id="owner-a")

    import pymupdf

    document = pymupdf.open()
    document.new_page()
    image_only_pdf = document.tobytes()
    document.close()
    with pytest.raises(PDFProcessingError, match="OCR"):
        rag_service.index_pdf(image_only_pdf, "scan.pdf", owner_id="owner-a")


def test_legacy_index_is_not_visible_to_new_sessions(tmp_path):
    index_path = tmp_path / "legacy.json"
    index_path.write_text(
        json.dumps(
            {
                "version": 1,
                "embedding_model": "mock-embedding",
                "documents": {
                    "legacy-doc": {
                        "document_id": "legacy-doc",
                        "filename": "legacy.pdf",
                        "indexed_at": "2026-01-01T00:00:00+00:00",
                    }
                },
                "chunks": [],
            }
        ),
        encoding="utf-8",
    )

    service = RAGService(
        api_key=None,
        base_url="https://example.invalid",
        embedding_model="mock-embedding",
        chat_model="mock-chat",
        index_path=index_path,
    )
    assert service.list_documents("new-session") == []
