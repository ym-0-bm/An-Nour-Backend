import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prisma.errors import RecordNotFoundError, UniqueViolationError

logger = logging.getLogger("app.errors")


def get_request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if request_id is None:
        request_id = uuid.uuid4().hex
        request.state.request_id = request_id
    return request_id


def register_exception_handlers(app: FastAPI) -> None:
    """Réponses d'erreur homogènes ({"detail": ...}) sans détail interne pour le client."""

    @app.exception_handler(UniqueViolationError)
    async def unique_violation_handler(request: Request, exc: UniqueViolationError):
        logger.warning("Conflit d'unicité [%s] %s %s", get_request_id(request), request.method, request.url.path)
        return JSONResponse(status_code=409, content={"detail": "Cette ressource existe déjà"})

    @app.exception_handler(RecordNotFoundError)
    async def record_not_found_handler(request: Request, exc: RecordNotFoundError):
        return JSONResponse(status_code=404, content={"detail": "Ressource non trouvée"})

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        request_id = get_request_id(request)
        # Le détail technique reste dans les journaux serveur, jamais dans la réponse
        logger.exception("Erreur non gérée [%s] %s %s", request_id, request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Erreur interne du serveur", "request_id": request_id},
            headers={"X-Request-ID": request_id},
        )
