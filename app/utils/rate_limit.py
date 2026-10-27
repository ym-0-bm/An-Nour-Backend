"""
Limitation de débit en mémoire (fenêtre glissante).

L'état est propre au processus : suffisant pour une instance unique (Render),
à remplacer par un stockage partagé (Redis) si le service passe à plusieurs instances.
"""
import math
import time
from collections import defaultdict, deque
from typing import Callable, Deque, Dict, Optional

from fastapi import HTTPException, Request, status

from app.config import settings

# Nombre de clés au-delà duquel les entrées expirées sont purgées
_PURGE_THRESHOLD = 10_000


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)

    def _prune(self, key: str, window: float) -> Deque[float]:
        hits = self._hits[key]
        threshold = self.clock() - window
        while hits and hits[0] <= threshold:
            hits.popleft()
        return hits

    def retry_after(self, key: str, limit: int, window: float) -> Optional[int]:
        """Secondes à attendre si la limite est atteinte, sinon None (sans enregistrer d'appel)."""
        hits = self._prune(key, window)
        if len(hits) < limit:
            return None
        return max(1, math.ceil(hits[0] + window - self.clock()))

    def add(self, key: str) -> None:
        if len(self._hits) > _PURGE_THRESHOLD:
            self._purge()
        self._hits[key].append(self.clock())

    def hit(self, key: str, limit: int, window: float) -> Optional[int]:
        """Enregistre un appel ; renvoie le délai d'attente si la limite est déjà atteinte."""
        wait = self.retry_after(key, limit, window)
        if wait is None:
            self.add(key)
        return wait

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)

    def clear(self) -> None:
        self._hits.clear()

    def _purge(self) -> None:
        # Fenêtre maximale utilisée par l'application : 15 minutes
        threshold = self.clock() - 15 * 60
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= threshold]:
            del self._hits[key]


limiter = RateLimiter()


def client_ip_candidates(request: Request) -> dict:
    """IP du client selon chaque source possible (diagnostic T3)."""
    forwarded = [ip.strip() for ip in request.headers.get("x-forwarded-for", "").split(",") if ip.strip()]
    return {
        "forwarded_last": forwarded[-1] if forwarded else None,
        "forwarded_first": forwarded[0] if forwarded else None,
        "cf-connecting-ip": request.headers.get("cf-connecting-ip"),
        "true-client-ip": request.headers.get("true-client-ip"),
        "direct": request.client.host if request.client else None,
    }


def client_ip(request: Request) -> str:
    """
    IP du client selon CLIENT_IP_SOURCE. Par défaut, la dernière entrée de X-Forwarded-For (ajoutée par
    le proxy) : les précédentes peuvent être forgées par le client. Si la source choisie est absente,
    repli sur la connexion directe.
    """
    candidates = client_ip_candidates(request)
    return candidates.get(settings.CLIENT_IP_SOURCE) or candidates["direct"] or "inconnu"


def too_many_requests(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Trop de requêtes, veuillez réessayer plus tard",
        headers={"Retry-After": str(retry_after)},
    )


def rate_limit(name: str, limit: int, window_seconds: int):
    """Dépendance FastAPI : au plus `limit` appels par IP sur `window_seconds` pour la route `name`."""

    async def dependency(request: Request):
        wait = limiter.hit(f"{name}:{client_ip(request)}", limit, window_seconds)
        if wait is not None:
            raise too_many_requests(wait)

    dependency.rate_limit = (name, limit, window_seconds)
    return dependency
