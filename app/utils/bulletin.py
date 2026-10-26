from typing import Dict, List, Optional

from app.database import prisma

# Spécification B6-B11 :
# - moyenne = (test d'entrée + évaluation 1 + évaluation 2 + évaluation 3) / 4, sans coefficient ;
# - une note manquante ou invalide n'est jamais assimilée à zéro : la moyenne n'est pas calculée ;
# - ex aequo : même rang, puis saut (1, 1, 3, 3, 5).
NOTE_MIN = 0
NOTE_MAX = 20
NB_EVALUATIONS = 3

LIBELLES = {
    "test_entree": "test d'entrée",
    "evaluation_1": "évaluation 1",
    "evaluation_2": "évaluation 2",
    "evaluation_3": "évaluation 3",
}


def _par_date(notes: list) -> list:
    return sorted(notes, key=lambda n: n.created_at)


def analyser_notes(notes: list) -> dict:
    """
    Retourne {"moyenne", "statut", "notes_manquantes", "notes_invalides"}.
    statut : "complet" (moyenne calculée), "incomplet" (note manquante) ou "invalide" (note hors échelle).
    """
    entrees = _par_date([n for n in notes if n.type == "TEST_ENTREE"])
    evaluations = _par_date([n for n in notes if n.type == "EVALUATION"])[:NB_EVALUATIONS]

    retenues = {"test_entree": entrees[0] if entrees else None}
    for i in range(NB_EVALUATIONS):
        retenues[f"evaluation_{i + 1}"] = evaluations[i] if i < len(evaluations) else None

    manquantes = [k for k, n in retenues.items() if n is None]
    invalides = [k for k, n in retenues.items()
                 if n is not None and (n.note is None or not NOTE_MIN <= n.note <= NOTE_MAX)]

    if manquantes or invalides:
        return {
            "moyenne": None,
            "statut": "incomplet" if manquantes else "invalide",
            "notes_manquantes": manquantes,
            "notes_invalides": invalides,
        }

    total = sum(n.note for n in retenues.values())
    return {
        "moyenne": round(total / len(retenues), 2),
        "statut": "complet",
        "notes_manquantes": [],
        "notes_invalides": [],
    }


def calculer_moyenne(notes: list) -> Optional[float]:
    """Moyenne sur les 4 notes, ou None si une note manque ou est invalide."""
    return analyser_notes(notes)["moyenne"]


def get_mention(moyenne: Optional[float]) -> Optional[str]:
    if moyenne is None:
        return None
    if moyenne >= 16:
        return "Excellent"
    elif moyenne >= 14:
        return "Très bien"
    elif moyenne >= 12:
        return "Bien"
    elif moyenne >= 10:
        return "Passable"
    return "Insuffisant"


def classer(moyennes: Dict[str, float]) -> Dict[str, int]:
    """Rang « olympique » : ex aequo au même rang, puis saut (18, 18, 16 → 1, 1, 3)."""
    ordre = sorted(moyennes.items(), key=lambda item: item[1], reverse=True)
    rangs: Dict[str, int] = {}
    precedente, rang_precedent = None, 0
    for position, (matricule, moyenne) in enumerate(ordre, start=1):
        rang = rang_precedent if moyenne == precedente else position
        rangs[matricule] = rang
        precedente, rang_precedent = moyenne, rang
    return rangs


async def calculer_rangs(niveau: str = None):
    """
    Calculer les rangs des séminaristes ayant une moyenne complète.
    Si niveau est fourni, le classement est fait uniquement parmi les séminaristes de ce niveau.
    Retourne (rangs, effectif classé).
    """
    if niveau:
        seminaristes = await prisma.seminariste.find_many(where={"niveau": niveau})
        matricules = [s.matricule for s in seminaristes]
        if not matricules:
            return {}, 0
    else:
        registrations = await prisma.registration.find_many()
        matricules = [r.matricule for r in registrations]

    # Toutes les notes en une seule requête (au lieu d'une requête par séminariste)
    notes = await prisma.note.find_many(where={"matricule": {"in": matricules}}) if matricules else []
    notes_par_matricule: Dict[str, List] = {m: [] for m in matricules}
    for note in notes:
        notes_par_matricule.setdefault(note.matricule, []).append(note)

    moyennes = {}
    for matricule, notes_seminariste in notes_par_matricule.items():
        moyenne = calculer_moyenne(notes_seminariste)
        if moyenne is not None:
            moyennes[matricule] = moyenne

    return classer(moyennes), len(moyennes)
