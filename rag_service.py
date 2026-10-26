from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pymupdf
from openai import OpenAI


class RAGConfigurationError(RuntimeError):
    pass


class PDFProcessingError(ValueError):
    pass


class DocumentNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class SearchResult:
    chunk_id: str
    document_id: str
    filename: str
    page: int
    text: str
    score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "filename": self.filename,
            "page": self.page,
            "text": self.text,
            "score": round(self.score, 6),
        }


def split_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """按字符数切分文本，并保留原项目中相邻块重叠的行为。"""
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 必须大于等于 0 且小于 chunk_size")

    normalized = re.sub(r"[ \t]+", " ", text.replace("\x00", ""))
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    if not normalized:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))
        chunks.append(normalized[start:end])
        if end == len(normalized):
            break
        start = end - overlap
    return chunks


def cosine_similarity(a: list[float] | np.ndarray, b: list[float] | np.ndarray) -> float:
    """复用原检索测试中的余弦相似度算法。"""
    vector_a = np.asarray(a, dtype=np.float32)
    vector_b = np.asarray(b, dtype=np.float32)
    denominator = float(np.linalg.norm(vector_a) * np.linalg.norm(vector_b))
    if denominator == 0:
        return 0.0
    return float(np.dot(vector_a, vector_b) / denominator)


class RAGService:
    INDEX_VERSION = 1

    def __init__(
        self,
        api_key: str | None,
        base_url: str,
        embedding_model: str,
        chat_model: str,
        index_path: Path,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.embedding_model = embedding_model
        self.chat_model = chat_model
        self.index_path = index_path
        self.embedding_batch_size = max(
            1,
            int(os.getenv("EMBEDDING_BATCH_SIZE", "10")),
        )
        self._client = (
            OpenAI(api_key=api_key, base_url=base_url) if api_key else None
        )
        self._documents: dict[str, dict[str, Any]] = {}
        self._chunks: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._load_warning: str | None = None
        self._load_index()

    def _require_client(self) -> OpenAI:
        if self._client is None:
            raise RAGConfigurationError(
                "未配置 DASHSCOPE_API_KEY，请在 .env 中配置后重启服务"
            )
        return self._client

    def _load_index(self) -> None:
        if not self.index_path.exists():
            return

        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            if payload.get("version") != self.INDEX_VERSION:
                raise ValueError("索引版本不兼容")
            stored_model = payload.get("embedding_model")
            if stored_model and stored_model != self.embedding_model:
                raise ValueError(
                    f"索引使用 {stored_model}，当前配置为 {self.embedding_model}"
                )
            self._documents = payload.get("documents", {})
            self._chunks = payload.get("chunks", [])
        except Exception as exc:
            # 不让损坏的缓存阻止 API 启动，也不覆盖原文件。
            self._load_warning = f"现有索引未加载：{exc}"
            self._documents = {}
            self._chunks = []

    def _save_index_locked(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.INDEX_VERSION,
            "embedding_model": self.embedding_model,
            "documents": self._documents,
            "chunks": self._chunks,
        }
        temporary_path = self.index_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temporary_path, self.index_path)

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        client = self._require_client()
        embeddings: list[list[float]] = []
        for start in range(0, len(texts), self.embedding_batch_size):
            batch = texts[start : start + self.embedding_batch_size]
            response = client.embeddings.create(
                model=self.embedding_model,
                input=batch,
            )
            ordered = sorted(response.data, key=lambda item: item.index)
            if len(ordered) != len(batch):
                raise RuntimeError("向量服务返回的结果数量与输入不一致")
            embeddings.extend(item.embedding for item in ordered)
        return embeddings

    @staticmethod
    def _extract_pages(pdf_bytes: bytes) -> list[tuple[int, str]]:
        try:
            document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:
            raise PDFProcessingError("PDF 解析失败，请检查文件是否有效") from exc

        try:
            if document.needs_pass:
                raise PDFProcessingError("暂不支持需要密码的 PDF 文件")
            pages = [
                (page_number, page.get_text("text"))
                for page_number, page in enumerate(document, start=1)
            ]
        finally:
            document.close()

        if not any(text.strip() for _, text in pages):
            raise PDFProcessingError(
                "PDF 中没有可提取文本；扫描版 PDF 需要先进行 OCR"
            )
        return pages

    def index_pdf(
        self,
        pdf_bytes: bytes,
        filename: str,
        chunk_size: int = 500,
        overlap: int = 100,
    ) -> dict[str, Any]:
        if overlap >= chunk_size:
            raise ValueError("overlap 必须小于 chunk_size")

        safe_filename = Path(filename).name
        document_id = hashlib.sha256(pdf_bytes).hexdigest()[:20]

        with self._lock:
            existing = self._documents.get(document_id)
            if (
                existing
                and existing.get("chunk_size") == chunk_size
                and existing.get("overlap") == overlap
            ):
                existing_chunks = [
                    chunk for chunk in self._chunks
                    if chunk["document_id"] == document_id
                ]
                return self._upload_response(existing, existing_chunks, reused=True)

        pages = self._extract_pages(pdf_bytes)
        prepared_chunks: list[dict[str, Any]] = []
        for page_number, page_text in pages:
            for text in split_text(page_text, chunk_size, overlap):
                prepared_chunks.append(
                    {
                        "page": page_number,
                        "text": text,
                    }
                )

        if not prepared_chunks:
            raise PDFProcessingError("PDF 中没有可用于建立索引的文本")

        vectors = self._embed_texts([chunk["text"] for chunk in prepared_chunks])
        if len(vectors) != len(prepared_chunks):
            raise RuntimeError("文本块数量和向量数量不一致")

        indexed_at = datetime.now(timezone.utc).isoformat()
        stored_chunks: list[dict[str, Any]] = []
        for index, (chunk, vector) in enumerate(zip(prepared_chunks, vectors)):
            stored_chunks.append(
                {
                    "chunk_id": f"{document_id}:{index}",
                    "document_id": document_id,
                    "filename": safe_filename,
                    "page": chunk["page"],
                    "text": chunk["text"],
                    "vector": vector,
                }
            )

        document_info = {
            "document_id": document_id,
            "filename": safe_filename,
            "pages": len(pages),
            "text_length": sum(len(text) for _, text in pages),
            "chunk_count": len(stored_chunks),
            "chunk_size": chunk_size,
            "overlap": overlap,
            "embedding_dimension": len(vectors[0]),
            "embedding_model": self.embedding_model,
            "indexed_at": indexed_at,
        }

        with self._lock:
            # 向量化全部成功后才替换旧索引，避免半成品状态。
            self._chunks = [
                chunk for chunk in self._chunks
                if chunk["document_id"] != document_id
            ]
            self._chunks.extend(stored_chunks)
            self._documents[document_id] = document_info
            self._save_index_locked()

        return self._upload_response(document_info, stored_chunks, reused=False)

    @staticmethod
    def _upload_response(
        document: dict[str, Any],
        chunks: list[dict[str, Any]],
        reused: bool,
    ) -> dict[str, Any]:
        overlap = int(document["overlap"])
        texts = [chunk["text"] for chunk in chunks]
        return {
            **document,
            "reused": reused,
            # 保留原上传接口返回 chunks 和 overlap_check 的能力。
            "chunks": texts,
            "overlap_check": (
                texts[0][-overlap:] == texts[1][:overlap]
                if overlap > 0 and len(texts) >= 2
                else None
            ),
        }

    def search(
        self,
        question: str,
        top_k: int = 3,
        document_id: str | None = None,
        min_score: float | None = None,
    ) -> list[dict[str, Any]]:
        question = question.strip()
        if not question:
            raise ValueError("问题不能为空")

        with self._lock:
            if document_id and document_id not in self._documents:
                raise DocumentNotFoundError(f"未找到文档索引：{document_id}")
            candidates = [
                chunk.copy()
                for chunk in self._chunks
                if document_id is None or chunk["document_id"] == document_id
            ]

        if not candidates:
            return []

        question_vector = self._embed_texts([question])[0]
        results = [
            SearchResult(
                chunk_id=chunk["chunk_id"],
                document_id=chunk["document_id"],
                filename=chunk["filename"],
                page=int(chunk["page"]),
                text=chunk["text"],
                score=cosine_similarity(question_vector, chunk["vector"]),
            )
            for chunk in candidates
        ]
        results.sort(key=lambda item: item.score, reverse=True)
        if min_score is not None:
            results = [item for item in results if item.score >= min_score]
        return [item.as_dict() for item in results[:top_k]]

    def answer(
        self,
        question: str,
        top_k: int = 3,
        document_id: str | None = None,
        min_score: float | None = None,
        use_rag: bool = True,
    ) -> dict[str, Any]:
        question = question.strip()
        if not question:
            raise ValueError("问题不能为空")

        client = self._require_client()
        sources = (
            self.search(question, top_k, document_id, min_score)
            if use_rag
            else []
        )

        if sources:
            context = "\n\n".join(
                f"[来源{index}｜{source['filename']}｜第{source['page']}页]\n"
                f"{source['text']}"
                for index, source in enumerate(sources, start=1)
            )
            system_prompt = (
                "你是严谨的文档问答助手。只能根据给定资料回答；"
                "资料不足时明确回答“根据已上传文档无法确定”。"
                "回答中的关键事实请使用[来源1]这样的标记引用资料，禁止编造。"
            )
            user_prompt = f"资料：\n{context}\n\n问题：{question}"
            mode = "rag"
        else:
            # 没有文档时保留原有通用问答能力；响应会明确标记为 general。
            system_prompt = "你是一个专业的智能问答助手。"
            user_prompt = question
            mode = "general"

        response = client.chat.completions.create(
            model=self.chat_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )
        answer_text = response.choices[0].message.content or ""
        return {
            "question": question,
            "answer": answer_text,
            "mode": mode,
            "sources": sources,
            "model": self.chat_model,
        }

    def list_documents(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(
                (document.copy() for document in self._documents.values()),
                key=lambda item: item["indexed_at"],
                reverse=True,
            )

    def delete_document(self, document_id: str) -> None:
        with self._lock:
            if document_id not in self._documents:
                raise DocumentNotFoundError(f"未找到文档索引：{document_id}")
            del self._documents[document_id]
            self._chunks = [
                chunk for chunk in self._chunks
                if chunk["document_id"] != document_id
            ]
            self._save_index_locked()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "api_key_configured": bool(self.api_key),
                "embedding_model": self.embedding_model,
                "chat_model": self.chat_model,
                "document_count": len(self._documents),
                "chunk_count": len(self._chunks),
                "index_path": str(self.index_path),
                "index_load_warning": self._load_warning,
            }
