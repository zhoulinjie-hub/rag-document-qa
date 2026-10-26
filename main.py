import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from rag_service import (
    DocumentNotFoundError,
    PDFProcessingError,
    RAGConfigurationError,
    RAGService,
)
from security import (
    DemoAuthenticationError,
    DemoRateLimitError,
    DemoSecurity,
    DemoSession,
)


PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("rag-api")

MAX_PDF_BYTES = int(os.getenv("MAX_PDF_BYTES", str(10 * 1024 * 1024)))
DEFAULT_MIN_SCORE = min(1.0, max(-1.0, float(os.getenv("RAG_MIN_SCORE", "0.25"))))
REQUESTS_PER_MINUTE = max(1, int(os.getenv("DEMO_REQUESTS_PER_MINUTE", "60")))
UPLOADS_PER_HOUR = max(1, int(os.getenv("DEMO_UPLOADS_PER_HOUR", "5")))
ASKS_PER_MINUTE = max(1, int(os.getenv("DEMO_ASKS_PER_MINUTE", "20")))
LOGINS_PER_10_MINUTES = max(
    1,
    int(os.getenv("DEMO_LOGINS_PER_10_MINUTES", "10")),
)

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
demo_security = DemoSecurity.from_environment(PROJECT_DIR)
bearer_scheme = HTTPBearer(auto_error=False)
STATIC_DIR = PROJECT_DIR / "static"

app = FastAPI(
    title="智能文档问答系统",
    description="PDF 上传、文本切分、向量化、相似度检索和大模型问答的一体化 RAG API",
    version="2.0.0",
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class SessionRequest(BaseModel):
    access_code: str = Field(min_length=1, max_length=512)


class SearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=3, ge=1, le=10)
    document_id: str | None = None
    min_score: float | None = Field(default=DEFAULT_MIN_SCORE, ge=-1.0, le=1.0)


class QuestionRequest(SearchRequest):
    use_rag: bool = True


def raise_rate_limit(exc: DemoRateLimitError) -> None:
    raise HTTPException(
        status_code=429,
        detail="请求过于频繁，请稍后重试",
        headers={"Retry-After": str(exc.retry_after)},
    ) from exc


def require_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> DemoSession:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="请先输入演示访问码")
    try:
        session = demo_security.verify_session(credentials.credentials)
        demo_security.rate_limiter.check(
            f"request:{session.session_id}",
            REQUESTS_PER_MINUTE,
            60,
        )
        return session
    except DemoRateLimitError as exc:
        raise_rate_limit(exc)
    except DemoAuthenticationError as exc:
        raise HTTPException(status_code=401, detail="登录状态无效或已过期") from exc


def enforce_session_limit(
    session: DemoSession,
    scope: str,
    limit: int,
    window_seconds: int,
) -> None:
    try:
        demo_security.rate_limiter.check(
            f"{scope}:{session.session_id}",
            limit,
            window_seconds,
        )
    except DemoRateLimitError as exc:
        raise_rate_limit(exc)


def raise_service_error(exc: Exception, operation: str) -> None:
    if isinstance(exc, RAGConfigurationError):
        raise HTTPException(status_code=503, detail="模型服务尚未完成配置") from exc
    if isinstance(exc, DocumentNotFoundError):
        raise HTTPException(status_code=404, detail="未找到指定文档") from exc
    if isinstance(exc, PDFProcessingError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail="请求参数不合法") from exc

    logger.exception("%s failed", operation, exc_info=exc)
    raise HTTPException(
        status_code=502,
        detail=f"{operation}失败，请稍后重试",
    ) from exc


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    )
    return response


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/health")
def health():
    configured = rag_service.status()["api_key_configured"]
    return {
        "status": "ok" if configured else "degraded",
        "model_service_configured": configured,
        "access_control": "enabled",
    }


@app.post("/session")
def create_session(request: SessionRequest, http_request: Request):
    client_host = http_request.client.host if http_request.client else "unknown"
    try:
        demo_security.rate_limiter.check(
            f"login:{client_host}",
            LOGINS_PER_10_MINUTES,
            600,
        )
        token, session = demo_security.create_session(request.access_code)
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_at": session.expires_at,
        }
    except DemoRateLimitError as exc:
        raise_rate_limit(exc)
    except DemoAuthenticationError as exc:
        raise HTTPException(status_code=401, detail="访问码不正确") from exc


@app.get("/documents")
def list_documents(session: DemoSession = Depends(require_session)):
    return {"documents": rag_service.list_documents(session.session_id)}


@app.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    chunk_size: int = Query(default=500, ge=100, le=2000),
    overlap: int = Query(default=100, ge=0, le=500),
    session: DemoSession = Depends(require_session),
):
    enforce_session_limit(session, "upload", UPLOADS_PER_HOUR, 3600)
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="只支持上传 PDF 文件")

    pdf_bytes = await file.read(MAX_PDF_BYTES + 1)
    await file.close()

    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="上传的 PDF 文件为空")
    if len(pdf_bytes) > MAX_PDF_BYTES:
        limit_mb = max(1, MAX_PDF_BYTES // (1024 * 1024))
        raise HTTPException(status_code=413, detail=f"PDF 文件不能超过 {limit_mb}MB")

    try:
        return await run_in_threadpool(
            rag_service.index_pdf,
            pdf_bytes,
            file.filename,
            session.session_id,
            chunk_size,
            overlap,
        )
    except Exception as exc:
        raise_service_error(exc, "PDF 解析或向量化")


@app.post("/search")
def search(
    request: SearchRequest,
    session: DemoSession = Depends(require_session),
):
    try:
        results = rag_service.search(
            question=request.question,
            owner_id=session.session_id,
            top_k=request.top_k,
            document_id=request.document_id,
            min_score=request.min_score,
        )
        return {"question": request.question, "results": results}
    except Exception as exc:
        raise_service_error(exc, "向量检索")


@app.post("/ask")
def ask_question(
    request: QuestionRequest,
    session: DemoSession = Depends(require_session),
):
    enforce_session_limit(session, "ask", ASKS_PER_MINUTE, 60)
    try:
        return rag_service.answer(
            question=request.question,
            owner_id=session.session_id,
            top_k=request.top_k,
            document_id=request.document_id,
            min_score=request.min_score,
            use_rag=request.use_rag,
        )
    except Exception as exc:
        raise_service_error(exc, "大模型问答")


@app.delete("/documents/{document_id}")
def delete_document(
    document_id: str,
    session: DemoSession = Depends(require_session),
):
    try:
        rag_service.delete_document(document_id, session.session_id)
        return {"message": "文档索引已删除", "document_id": document_id}
    except Exception as exc:
        raise_service_error(exc, "删除文档索引")
