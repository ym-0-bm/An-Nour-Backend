from app.database import prisma

Note = []

def calculer_moyenne(notes: list[Note]) -> float:
    """
    Calcule la moyenne avec 4 notes:
    - Test d'entrée
    - Évaluation 1
    - Évaluation 2
    - Évaluation 3
    Toujours divisée par 4, même si des notes sont manquantes (comptées comme 0).
    """
    # Récupérer le test d'entrée (0 si absent)
    note_entree = next((n.note for n in notes if n.type == "TEST_ENTREE"), 0)
    
    # Récupérer les notes d'évaluation triées par date de création
    evaluations = sorted(
        [n for n in notes if n.type == "EVALUATION"],
        key=lambda n: n.created_at
    )
    
    # Prendre les 3 premières évaluations (0 si manquantes)
    note1 = evaluations[0].note if len(evaluations) > 0 else 0
    note2 = evaluations[1].note if len(evaluations) > 1 else 0
    note3 = evaluations[2].note if len(evaluations) > 2 else 0
    
    # Calculer la moyenne sur 4 notes (toujours divisé par 4)
    total = note_entree + note1 + note2 + note3
    return round(total / 4, 2)

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