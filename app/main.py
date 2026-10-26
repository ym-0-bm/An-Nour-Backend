from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from app.config import settings
from app.database import connect_db, disconnect_db
from app.utils.errors import get_request_id, register_exception_handlers
from app.routes import registrations, scientific, finance, admin, visiteurs, feedback, sante

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await connect_db()
    yield
    # Shutdown
    await disconnect_db()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="2.0.0",
    lifespan=lifespan
)

register_exception_handlers(app)

# Taille maximale d'une requête : une image + la marge du multipart
MAX_REQUEST_BYTES = settings.MAX_UPLOAD_BYTES + 1024 * 1024

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    request_id = get_request_id(request)

    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_REQUEST_BYTES:
        response = JSONResponse(status_code=413, content={"detail": "Requête trop volumineuse"})
    else:
        response = await call_next(request)

    response.headers["X-Request-ID"] = request_id
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    # Les réponses de l'API contiennent des données personnelles : pas de cache
    response.headers.setdefault("Cache-Control", "no-store")
    if settings.ENVIRONMENT == "production":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


# CORS (ajouté en dernier : enveloppe toutes les réponses, y compris les 413)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["X-Request-ID", "Retry-After", "X-Total-Count"],
)

# Routes
app.include_router(registrations.router, prefix=settings.API_V1_STR)
app.include_router(scientific.router, prefix=settings.API_V1_STR)
app.include_router(finance.router, prefix=settings.API_V1_STR)
app.include_router(admin.router, prefix=settings.API_V1_STR)
app.include_router(visiteurs.router, prefix=settings.API_V1_STR)
app.include_router(feedback.router, prefix=settings.API_V1_STR)
app.include_router(sante.router, prefix=settings.API_V1_STR)

@app.get("/")
async def root():
    return {"message": "An Nour Management System API", "version": "2.0.0"}

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "database": "MongoDB",
        "modules": {
            "inscriptions": "active",
            "scientifique": "active",
            "finances": "active",
            "visiteurs": "active",
            "feedback": "active"
        }
    }