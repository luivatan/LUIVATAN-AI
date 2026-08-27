"""Apex AI HTTP foundation. Run with: uvicorn api_server:app --reload"""
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException, Response, Cookie, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from apex_documents import DocumentQueue, DocumentError
from apex_security import secure_upload
from apex_retrieval import EmbeddingSystem, ChromaStore, RetrievalError
from apex_answers import AnswerEngine, AnswerError
from pydantic import BaseModel
from apex_auth import AuthService, AccountError

app = FastAPI(title="Apex AI API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:4173", "http://127.0.0.1:4173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
auth = AuthService(os.getenv("ACCOUNT_DATABASE", "accounts.sqlite3"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploaded_pdfs"))
document_queue = DocumentQueue()
_embeddings = None
_store = None

def retrieval_store():
    global _embeddings, _store
    if _store is None:
        _embeddings = EmbeddingSystem()
        _store = ChromaStore(os.getenv("DATABASE_PATH", "database"), embeddings=_embeddings)
    return _store

class Registration(BaseModel):
    email: str
    password: str
    display_name: str
class Login(BaseModel):
    email: str
    password: str
class ResetRequest(BaseModel): email: str
class Reset(BaseModel): token: str; password: str
class Question(BaseModel): question: str

def fail(exc): raise HTTPException(status_code=400, detail=str(exc))

def require_user(apex_session):
    if not apex_session: raise HTTPException(status_code=401, detail="Authentication required.")
    try: return auth.session_user(apex_session)
    except AccountError as exc: raise HTTPException(status_code=401, detail=str(exc))

@app.get("/api/health")
def health(): return {"status": "ok", "service": "apex-ai"}

@app.post("/api/documents")
async def upload_document(file: UploadFile = File(...), apex_session: str | None = Cookie(default=None)):
    require_user(apex_session)
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF documents are supported.")
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temp:
            temp.write(await file.read())
            temporary = Path(temp.name)
        saved = secure_upload(temporary, UPLOAD_DIR)
        doc = document_queue.submit(saved)
        return {"id": doc.id, "filename": doc.filename, "status": doc.status}
    except (DocumentError, OSError) as exc: raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if 'temporary' in locals(): temporary.unlink(missing_ok=True)

@app.get("/api/documents")
def documents(apex_session: str | None = Cookie(default=None)):
    require_user(apex_session)
    return [{"id": d.id, "filename": d.filename, "status": d.status, "pages": d.pages, "chunks": len(d.chunks), "error": d.error} for d in document_queue.list_documents()]

@app.post("/api/questions")
def ask_question(body: Question, apex_session: str | None = Cookie(default=None)):
    require_user(apex_session)
    if not body.question.strip(): raise HTTPException(status_code=400, detail="Ask a question first.")
    try:
        results = retrieval_store().search(body.question, limit=8)
        # The configured provider is resolved lazily; this keeps health/auth usable without a model.
        from apex_llm import ModelConfig, ModelManager
        manager = ModelManager()
        answerer = AnswerEngine(manager.generator(ModelConfig.from_env()))
        answer, citations = answerer.answer(body.question, results)
        return {"answer": answer, "citations": [{"number": c.number, "source": c.source, "page": c.page, "text": c.text} for c in citations]}
    except (RetrievalError, AnswerError) as exc: raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc: raise HTTPException(status_code=503, detail="AI service is not ready. Check the model configuration.") from exc

@app.post("/api/auth/register")
def register(body: Registration):
    try: token = auth.register(body.email, body.password, body.display_name)
    except AccountError as exc: fail(exc)
    # Email delivery belongs to the deployment adapter; do not expose this token in production.
    return {"message": "Account created. Verify your email to continue.", "verification_token": token}

@app.post("/api/auth/verify")
def verify(token: str):
    try: auth.verify_email(token)
    except AccountError as exc: fail(exc)
    return {"message": "Email verified."}

@app.post("/api/auth/login")
def login(body: Login, response: Response):
    try: token = auth.login(body.email, body.password)
    except AccountError as exc: fail(exc)
    response.set_cookie("apex_session", token, httponly=True, samesite="lax", secure=False, max_age=3600)
    return {"message": "Signed in."}

@app.post("/api/auth/logout")
def logout(response: Response, apex_session: str | None = Cookie(default=None)):
    if apex_session: auth.logout(apex_session)
    response.delete_cookie("apex_session")
    return {"message": "Signed out."}

@app.get("/api/me")
def me(apex_session: str | None = Cookie(default=None)):
    if not apex_session: raise HTTPException(status_code=401, detail="Authentication required.")
    try: return auth.session_user(apex_session)
    except AccountError as exc: raise HTTPException(status_code=401, detail=str(exc))

@app.post("/api/auth/password-reset/request")
def reset_request(body: ResetRequest):
    auth.request_password_reset(body.email)
    return {"message": "If the account exists, reset instructions will be sent."}

@app.post("/api/auth/password-reset")
def reset(body: Reset):
    try: auth.reset_password(body.token, body.password)
    except AccountError as exc: fail(exc)
    return {"message": "Password updated."}
