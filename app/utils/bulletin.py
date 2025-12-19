from app.database import prisma

Note = []

def calculer_moyenne(notes: list[Note]) -> float:
    notes_valides = [n.note for n in notes if n.type != "TEST_ENTREE"]

    if not notes_valides:
        return 0

    return round(sum(notes_valides) / len(notes_valides), 2)

def get_mention(moyenne: float) -> str:
    if moyenne >= 16:
        return "Excellent"
    elif moyenne >= 14:
        return "Très bien"
    elif moyenne >= 12:
        return "Bien"
    elif moyenne >= 10:
        return "Passable"
    return "Insuffisant"


async def calculer_rangs(niveau: str = None):
    """
    Calculer les rangs des séminaristes.
    Si niveau est fourni, le classement est fait uniquement parmi les séminaristes de ce niveau.
    """
    
    # Si un niveau est spécifié, récupérer uniquement les séminaristes de ce niveau
    if niveau:
        seminaristes = await prisma.seminariste.find_many(
            where={"niveau": niveau}
        )
        matricules = [s.matricule for s in seminaristes]
        
        if not matricules:
            return {}, 0
    else:
        # Sinon récupérer tous les séminaristes enregistrés
        registrations = await prisma.registration.find_many()
        matricules = [r.matricule for r in registrations]

    resultats = []

    for matricule in matricules:
        notes = await prisma.note.find_many(
            where={
                "matricule": matricule
            }
        )

        moyenne = calculer_moyenne(notes)
        resultats.append({
            "matricule": matricule,
            "moyenne": moyenne
        })

    # Trier par moyenne décroissante
    resultats.sort(key=lambda x: x["moyenne"], reverse=True)

    rangs = {}
    for index, r in enumerate(resultats, start=1):
        rangs[r["matricule"]] = index

    return rangs, len(resultats)