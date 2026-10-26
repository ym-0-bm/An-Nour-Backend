"""
Migration B10 : reporter dans le registre des transactions (used_transactions) les ID de transaction
des inscriptions existantes, afin qu'un ancien reçu ne puisse pas valider une nouvelle inscription.

Usage, depuis la racine du backend, avec le .env de l'environnement visé :
    python -m scripts.backfill_used_transactions            # simulation : aucune écriture
    python -m scripts.backfill_used_transactions --apply    # insertion (après sauvegarde et accord)

Propriétés : insertion seule (aucune modification ni suppression), idempotent (relançable),
les doublons existants sont signalés par matricule mais jamais modifiés automatiquement.
"""
import argparse
import asyncio
from collections import defaultdict

from prisma.errors import UniqueViolationError

from app.database import prisma


async def main(apply: bool):
    await prisma.connect()
    try:
        registrations = await prisma.registration.find_many(
            where={"transaction_id": {"not": None}, "payment_status": {"in": ["completed", "verifie"]}}
        )
        par_transaction = defaultdict(list)
        for r in registrations:
            if r.transaction_id.strip():
                par_transaction[r.transaction_id.strip()].append(r.matricule)

        doublons = {t: m for t, m in par_transaction.items() if len(m) > 1}
        deja, a_inserer = 0, []
        for transaction_id, matricules in par_transaction.items():
            if await prisma.usedtransaction.find_unique(where={"transaction_id": transaction_id}):
                deja += 1
            else:
                a_inserer.append((transaction_id, sorted(matricules)[0]))

        print(f"Inscriptions vérifiées avec un ID de transaction : {len(registrations)}")
        print(f"ID distincts : {len(par_transaction)} | déjà enregistrés : {deja} | à enregistrer : {len(a_inserer)}")
        if doublons:
            print(f"ATTENTION : {len(doublons)} ID partagés par plusieurs inscriptions (à examiner manuellement) :")
            for matricules in doublons.values():
                print("  - " + ", ".join(sorted(matricules)))

        if not apply:
            print("Simulation : aucune écriture. Relancer avec --apply après sauvegarde et accord.")
            return

        inseres = 0
        for transaction_id, matricule in a_inserer:
            try:
                await prisma.usedtransaction.create(data={"transaction_id": transaction_id, "matricule": matricule})
                inseres += 1
            except UniqueViolationError:
                pass  # inséré entre-temps (exécution concurrente ou relance)
        print(f"Insertions effectuées : {inseres}")
    finally:
        await prisma.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="écrire réellement (sinon simulation)")
    asyncio.run(main(parser.parse_args().apply))
