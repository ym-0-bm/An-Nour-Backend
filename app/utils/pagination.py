from fastapi import Query, Response

# B13 : listes bornées sans changer le format des réponses existantes (tableaux ou {total, data}).
# Le total complet est renvoyé dans l'en-tête X-Total-Count pour paginer au-delà de MAX_LIMIT.
MAX_LIMIT = 1000


class Page:
    def __init__(
        self,
        skip: int = Query(0, ge=0, description="Nombre d'éléments à sauter"),
        limit: int = Query(MAX_LIMIT, ge=1, le=MAX_LIMIT, description="Nombre maximal d'éléments"),
    ):
        self.skip = skip
        self.limit = limit


def set_total(response: Response, total: int) -> None:
    response.headers["X-Total-Count"] = str(total)
