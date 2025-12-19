from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.config import settings
from app.database import connect_db, disconnect_db
from app.routes import registrations, scientific, finance, admin, visiteurs

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
    allow_origins=[
        "*",                               # pour toute origine
        "http://localhost:3000",             # pour dev React
        "https://an-nour25.vercel.app",       # pour site front en production
        "https://www.an-nour25.com",
        "https://an-nour25-4yprk8vn3-mamadoutuo77-gmailcoms-projects.vercel.app",
        "http://annourpwa.netlify.app"
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Monter les fichiers statiques
app.mount("/media", StaticFiles(directory="media"), name="media")

# Routes
app.include_router(registrations.router, prefix=settings.API_V1_STR)
app.include_router(scientific.router, prefix=settings.API_V1_STR)
app.include_router(finance.router, prefix=settings.API_V1_STR)
app.include_router(admin.router, prefix=settings.API_V1_STR)
app.include_router(visiteurs.router, prefix=settings.API_V1_STR)

@app.get("/")
async def root():
    return {"message": "An Nour Management System API - MongoDB", "version": "1.0.0"}

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "database": "MongoDB",
        "modules": {
            "inscriptions": "active",
            "scientifique": "active",
            "finances": "active",
            "visiteurs": "active"
        }
    }