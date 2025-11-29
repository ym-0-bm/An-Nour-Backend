import asyncio
from prisma import Prisma

async def init_dortoirs():
    prisma = Prisma()
    await prisma.connect()

    dortoirs = [
        # 🧑 Dortoirs garçons (M)
        # {"code": "NASSR", "name": "Nassr – Victoire", "capacity": 30, "gender": "M"},
        # {"code": "BASIR", "name": "Basîr – Clairvoyance", "capacity": 30, "gender": "M"},
        # {"code": "HILM", "name": "Hilm – Maîtrise de soi", "capacity": 30, "gender": "M"},
        # {"code": "SIDANE", "name": "Sidane – Gardien", "capacity": 30, "gender": "M"},
        # {"code": "FURQAN", "name": "Furqân – Discernement", "capacity": 30, "gender": "M"},
        # {"code": "RIYADH", "name": "Riyâdh – Jardins", "capacity": 30, "gender": "M"},
        {"code": "PEPINIERE-G", "name": "Pépinière – Garçons", "capacity": 50, "gender": "M"},

        # 👩 Dortoirs filles (F)
        # {"code": "NAJMA", "name": "Najma – Étoile", "capacity": 30, "gender": "F"},
        # {"code": "HIDAYA", "name": "Hidaya – Guidance", "capacity": 30, "gender": "F"},
        # {"code": "RAHMA", "name": "Rahma – Miséricorde", "capacity": 30, "gender": "F"},
        # {"code": "SAKINA", "name": "Sakîna – Sérénité", "capacity": 30, "gender": "F"},
        # {"code": "SALWA", "name": "Salwa – Réconfort", "capacity": 30, "gender": "F"},
        # {"code": "ZAHRA", "name": "Zahra – Fleur/Pureté", "capacity": 30, "gender": "F"},
        # {"code": "FIRDAOUS", "name": "Firdaous", "capacity": 30, "gender": "F"},
        # {"code": "SALAM", "name": "Salam", "capacity": 30, "gender": "F"},
        {"code": "PEPINIERE-F", "name": "Pépinière – Filles", "capacity": 50, "gender": "F"},
    ]

    for dortoir in dortoirs:
        existing = await prisma.dortoir.find_unique(where={"code": dortoir["code"]})
        if not existing:
            await prisma.dortoir.create(data=dortoir)
            print(f"✅ Dortoir {dortoir['name']} ({dortoir['gender']}) créé")
        else:
            print(f"⚠️ Dortoir {dortoir['name']} existe déjà")

    print("\n✅ Initialisation terminée")
    await prisma.disconnect()


if __name__ == "__main__":
    asyncio.run(init_dortoirs())
