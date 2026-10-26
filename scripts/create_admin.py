"""
Créer un compte administrateur (amorçage, quand aucun admin ne peut en créer un autre).

Usage, depuis la racine du backend, avec le .env de l'environnement visé :
    python -m scripts.create_admin

Le mot de passe est saisi sans écho et n'est jamais affiché ni journalisé.
"""
import asyncio
import getpass

from app.database import prisma
from app.models.admin_schemas import UserCreate
from app.utils.auth import ROLE_ADMIN, get_password_hash


async def main():
    email = input("Email : ").strip()
    username = input("Nom d'utilisateur : ").strip()
    nom = input("Nom : ").strip()
    prenom = input("Prénom : ").strip()
    password = getpass.getpass("Mot de passe (8 caractères minimum) : ")
    if password != getpass.getpass("Confirmer le mot de passe : "):
        raise SystemExit("Les mots de passe ne correspondent pas.")

    data = UserCreate(
        email=email, username=username, password=password,
        nom=nom, prenom=prenom, role=ROLE_ADMIN
    )

    await prisma.connect()
    try:
        existing = await prisma.user.find_first(
            where={"OR": [{"email": data.email}, {"username": data.username}]}
        )
        if existing:
            raise SystemExit("Un compte existe déjà avec cet email ou ce nom d'utilisateur.")

        user = await prisma.user.create(
            data={
                "email": data.email,
                "username": data.username,
                "password": get_password_hash(data.password),
                "nom": data.nom,
                "prenom": data.prenom,
                "role": ROLE_ADMIN,
            }
        )
        print(f"Administrateur créé : {user.username}")
    finally:
        await prisma.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
