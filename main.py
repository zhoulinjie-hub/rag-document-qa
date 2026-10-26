import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from rag_service import (
    DocumentNotFoundError,
    PDFProcessingError,
    RAGConfigurationError,
    RAGService,
)


PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("rag-api")

MAX_PDF_BYTES = int(os.getenv("MAX_PDF_BYTES", str(10 * 1024 * 1024)))

rag_service = RAGService(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url=os.getenv(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-v4"),
    chat_model=os.getenv("CHAT_MODEL", "qwen-plus"),
    index_path=PROJECT_DIR / "data" / "rag_index.json",
)

app = FastAPI(
    title="智能文档问答系统",
    description="PDF 上传、文本切分、向量化、相似度检索和大模型问答的一体化 RAG API",
    version="1.0.0",
)


class SearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=3, ge=1, le=10)
    document_id: str | None = None
    min_score: float | None = Field(default=None, ge=-1.0, le=1.0)


class QuestionRequest(SearchRequest):
    use_rag: bool = True


def raise_service_error(exc: Exception, operation: str) -> None:
    if isinstance(exc, RAGConfigurationError):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(exc, DocumentNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (PDFProcessingError, ValueError)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.exception("%s failed", operation, exc_info=exc)
    raise HTTPException(
        status_code=502,
        detail=f"{operation}失败，请检查模型服务配置和终端日志",
    ) from exc


@app.get("/")
def home():
    # 保留原有首页响应，避免影响已经依赖该接口的调用方。
    return {"message": "智能问答系统启动成功"}


@app.get("/health")
def health():
    state = rag_service.status()
    return {
        "status": "ok" if state["api_key_configured"] else "degraded",
        **state,
    }


@app.get("/documents")
def list_documents():
    return {"documents": rag_service.list_documents()}


@app.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    chunk_size: int = Query(default=500, ge=100, le=2000),
    overlap: int = Query(default=100, ge=0, le=500),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="只支持上传 PDF 文件")

    pdf_bytes = await file.read(MAX_PDF_BYTES + 1)
    await file.close()

    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="上传的 PDF 文件为空")
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="PDF 文件不能超过 10MB")

    try:
        return await run_in_threadpool(
            rag_service.index_pdf,
            pdf_bytes,
            file.filename,
            chunk_size,
            overlap,
        )
    except Exception as exc:
        raise_service_error(exc, "PDF 解析或向量化")


@app.post("/search")
def search(request: SearchRequest):
    try:
        results = rag_service.search(
            question=request.question,
            top_k=request.top_k,
            document_id=request.document_id,
            min_score=request.min_score,
        )
        return {"question": request.question, "results": results}
    except Exception as exc:
        raise_service_error(exc, "向量检索")


@app.post("/ask")
def ask_question(request: QuestionRequest):
    try:
        return rag_service.answer(
            question=request.question,
            top_k=request.top_k,
            document_id=request.document_id,
            min_score=request.min_score,
            use_rag=request.use_rag,
        )
    except Exception as exc:
        raise_service_error(exc, "大模型问答")


@app.delete("/documents/{document_id}")
def delete_document(document_id: str):
    try:
        rag_service.delete_document(document_id)
        return {"message": "文档索引已删除", "document_id": document_id}
    except Exception as exc:
        raise_service_error(exc, "删除文档索引")
