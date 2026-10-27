"""
Journalisation centralisée (B17).

- Un seul format pour toute l'application, niveau réglable par LOG_LEVEL.
- Chaque ligne porte l'identifiant de la requête (X-Request-ID) pour relier les traces.
- Le journal d'accès n'inclut jamais la chaîne de requête (elle peut contenir des noms, ex. ?search=).
  Le journal d'accès d'uvicorn, qui l'inclut, est désactivé (--no-access-log dans le Dockerfile).
"""
import logging
import sys
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Bibliothèques bavardes : seulement les avertissements
    for noisy in ("httpx", "httpcore", "PIL", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
