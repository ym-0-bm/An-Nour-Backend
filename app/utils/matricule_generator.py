import re
from datetime import datetime, timezone

from prisma.errors import UniqueViolationError

from app.config import settings
from app.database import prisma

# Garde-fou : nombre maximal de numéros essayés pour un même appel
MAX_ESSAIS = 1000


def matricule_prefix(now: datetime = None) -> str:
    """
    Préfixe du matricule selon l'année (spécification B8) : ANNOUR26 pour 2026, ANNOUR27 pour 2027.
    MATRICULE_YEAR force l'année (édition préparée à l'avance) ; sinon l'année UTC courante est utilisée.
    """
    year = settings.MATRICULE_YEAR or (now or datetime.now(timezone.utc)).year
    return f"{settings.MATRICULE_PREFIX}{year % 100:02d}"


async def _max_existant(base: str) -> int:
    """Plus grand numéro déjà attribué pour cette base (préserve les matricules existants)."""
    existants = await prisma.registration.find_many(where={"matricule": {"startswith": f"{base}-"}})
    numeros = [int(m.group(1)) for r in existants if (m := re.fullmatch(rf"{re.escape(base)}-(\d+)", r.matricule))]
    return max(numeros, default=0)


async def _prochain_numero(base: str) -> int:
    """Incrément atomique du compteur ($inc) : deux appels simultanés n'obtiennent jamais le même numéro."""
    if await prisma.matriculecounter.find_unique(where={"key": base}) is None:
        try:
            await prisma.matriculecounter.create(data={"key": base, "value": await _max_existant(base)})
        except UniqueViolationError:
            pass  # créé entre-temps par un appel concurrent
    compteur = await prisma.matriculecounter.update(where={"key": base}, data={"value": {"increment": 1}})
    return compteur.value


async def generate_matricule(dortoir_code: str) -> str:
    """
    Génère un matricule unique au format : ANNOUR{AA}-{DORTOIR}-{NNN}
    Exemple : ANNOUR26-NASSR-001
    L'unicité est garantie par le compteur atomique et, en dernier recours, par l'index unique
    de Registration.matricule (voir la création d'inscription).
    """
    base = f"{matricule_prefix()}-{dortoir_code}"
    for _ in range(MAX_ESSAIS):
        matricule = f"{base}-{await _prochain_numero(base):03d}"
        if not await prisma.registration.find_unique(where={"matricule": matricule}):
            return matricule
    raise RuntimeError(f"Impossible de générer un matricule pour {base}")
