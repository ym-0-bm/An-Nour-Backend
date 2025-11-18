from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.config import settings
from app.database import connect_db, disconnect_db
from app.routes import registrations

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await connect_db()
    yield
    # Shutdown
    await disconnect_db()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="1.0.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Monter les fichiers statiques
app.mount("/media", StaticFiles(directory="media"), name="media")

# Routes
app.include_router(registrations.router, prefix=settings.API_V1_STR)

@app.get("/")
async def root():
    return {"message": "Inscription System API - MongoDB", "version": "1.0.0"}

@app.get("/health")
async def health():
    return {"status": "healthy", "database": "MongoDB"}