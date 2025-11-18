from app.database import prisma

async def generate_matricule(dortoir_code: str) -> str:
    """
    Génère un matricule unique au format : ANNOUR-DORTOIR-COUNTER
    Exemple : ANNOUR-NASSR-001
    Incrémente en cas de collision.
    """

    # Compter le nombre d'inscriptions existantes pour ce dortoir
    count = await prisma.registration.count(
        where={"dortoir_code": dortoir_code}
    )

    counter = count + 1

    while True:
        # Génération du matricule (3 chiffres)
        matricule = f"ANNOUR25-{dortoir_code}-{counter:03d}"

        # Vérifier s'il existe déjà
        existing = await prisma.registration.find_unique(
            where={"matricule": matricule}
        )

        if not existing:
            return matricule  # Unique → OK

        # Sinon, on incrémente et on essaye le suivant
        counter += 1
