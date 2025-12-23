import pandas as pd
import asyncio
from prisma import Prisma

OUTPUT_FILE = "export_seminaristes.xlsx"


async def export_seminaristes():
    prisma = Prisma()
    await prisma.connect()

    # 1️⃣ Récupération des séminaristes AVEC relations
    seminaristes = await prisma.seminariste.find_many(
        include={
            "registration": {
                "include": {
                    "dortoir": True
                }
            }
        }
    )

    if not seminaristes:
        print("❌ Aucun séminariste trouvé.")
        return

    # 2️⃣ Transformation en DataFrame
    rows = []

    for s in seminaristes:
        reg = s.registration
        dortoir = reg.dortoir if reg else None

        rows.append({
            "Matricule": s.matricule,
            "Nom": reg.nom if reg else "",
            "Prénom": reg.prenom if reg else "",
            "Sexe": reg.sexe if reg else "",
            "Âge": reg.age if reg else "",
            "Niveau Séminaire": s.niveau or "Non renseigné",
            "Niveau Académique": reg.niveau_academique if reg else "",
            "Dortoir": dortoir.name if dortoir else "Non attribué",
            "Code Dortoir": dortoir.code if dortoir else "",
            "Contact Parent": reg.contact_parent if reg else "",
        })

    df = pd.DataFrame(rows)

    # Sécurisation
    df["Dortoir"] = df["Dortoir"].fillna("Non attribué")
    df["Niveau Séminaire"] = df["Niveau Séminaire"].fillna("Non renseigné")

    # 3️⃣ Écriture Excel
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:

        # 📄 Vue globale
        df.to_excel(writer, sheet_name="Tous_les_Seminaristes", index=False)

        # 📄 Par dortoir
        df.sort_values("Dortoir").to_excel(
            writer, sheet_name="Par_Dortoir", index=False
        )

        # 📄 Par niveau séminaire
        df.sort_values("Niveau Séminaire").to_excel(
            writer, sheet_name="Par_Niveau_Seminaire", index=False
        )

        # 📄 Une feuille par dortoir
        for code, group in df.groupby("Code Dortoir"):
            sheet_name = f"DORTOIR_{code}"[:31]
            group.to_excel(writer, sheet_name=sheet_name, index=False)

        # 📄 Une feuille par niveau séminaire
        for niveau, group in df.groupby("Niveau Séminaire"):
            sheet_name = f"NIVEAU_{niveau}"[:31]
            group.to_excel(writer, sheet_name=sheet_name, index=False)

    await prisma.disconnect()

    print(f"✅ Export Excel généré avec succès : {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(export_seminaristes())
