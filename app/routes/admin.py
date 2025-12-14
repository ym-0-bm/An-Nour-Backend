from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Depends
from typing import Optional, List
from datetime import datetime, timedelta
import io
# import pandas as pd

from app.models.admin_schemas import (
    UserLogin, UserCreate, UserUpdate, UserResponse, Token,
    SeminaristeCreate, SeminaristeUpdate, SeminaristeResponse,
    DashboardStats, ImportResult, ExportParams,
    BadgeGenerate, DiplomeGenerate, ListePDFGenerate
)
from app.database import prisma
from app.utils.auth import (
    get_password_hash, verify_password, create_access_token,
    get_current_user, RequireAdmin
)

router = APIRouter(prefix="/admin", tags=["Administration"])


# ============================================
# AUTHENTIFICATION
# ============================================

@router.post("/auth/register", response_model=UserResponse, status_code=201)
async def register_user(data: UserCreate):
    """Créer un utilisateur (admin uniquement)"""

    # Vérifier unicité email
    existing_email = await prisma.user.find_unique(where={"email": data.email})
    if existing_email:
        raise HTTPException(status_code=409, detail="Cet email est déjà utilisé")

    # Vérifier unicité username
    existing_username = await prisma.user.find_unique(where={"username": data.username})
    if existing_username:
        raise HTTPException(status_code=409, detail="Ce nom d'utilisateur est déjà pris")

    # Hasher le mot de passe
    hashed_password = get_password_hash(data.password)

    # Créer l'utilisateur
    user = await prisma.user.create(
        data={
            "email": data.email,
            "username": data.username,
            "password": hashed_password,
            "nom": data.nom,
            "prenom": data.prenom,
            "role": data.role
        }
    )

    return user


@router.post("/auth/login", response_model=Token)
async def login(data: UserLogin):
    """Connexion avec email/password"""

    # Chercher utilisateur email ou username

    user = await prisma.user.find_first(
        where={
            "OR": [
                {"email": data.identifier},
                {"username": data.identifier}
            ]
        }
    )

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Email ou mot de passe incorrect"
        )

    # Vérifier mot de passe
    if not verify_password(data.password, user.password):
        raise HTTPException(
            status_code=401,
            detail="Email ou mot de passe incorrect"
        )

    # Vérifier si actif
    if not user.is_active:
        raise HTTPException(
            status_code=403,
            detail="Compte désactivé"
        )

    # Mettre à jour last_login
    await prisma.user.update(
        where={"id": user.id},
        data={"last_login": datetime.now()}
    )

    # Créer token
    access_token = create_access_token(
        data={
            "sub": user.id,
            "email": user.email,
            "role": user.role
        }
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user
    }


@router.get("/auth/me", response_model=UserResponse)
async def get_current_user_info(current_user=Depends(get_current_user)):
    """Récupérer infos utilisateur connecté"""
    return current_user


@router.post("/auth/logout")
async def logout(current_user=Depends(get_current_user)):
    """Déconnexion (côté client : supprimer le token)"""
    return {"message": "Déconnexion réussie"}


# ============================================
# GESTION UTILISATEURS
# ============================================

@router.get("/users", response_model=List[UserResponse])
async def get_users(
        role: Optional[str] = None,
        is_active: Optional[bool] = None
):
    """Liste des utilisateurs"""

    where = {}
    if role:
        where["role"] = role
    if is_active is not None:
        where["is_active"] = is_active

    users = await prisma.user.find_many(where=where)

    # Trier par nom
    users_sorted = sorted(users, key=lambda u: (u.nom, u.prenom))

    return users_sorted


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(user_id: str):
    """Détail d'un utilisateur"""

    user = await prisma.user.find_unique(where={"id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

    return user


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
        user_id: str,
        data: UserUpdate
):
    """Modifier un utilisateur"""

    existing = await prisma.user.find_unique(where={"id": user_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

    update_data = data.model_dump(exclude_unset=True)

    # Si mot de passe, le hasher
    if "password" in update_data:
        update_data["password"] = get_password_hash(update_data["password"])

    user = await prisma.user.update(
        where={"id": user_id},
        data=update_data
    )

    return user


@router.patch("/users/{user_id}")
async def delete_user(user_id: str):
    """Supprimer (désactiver) un utilisateur"""

    existing = await prisma.user.find_unique(where={"id": user_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

    # Ne pas supprimer soi-même
    # if user_id == current_user.id:
        raise HTTPException(
            status_code=400,
            detail="Vous ne pouvez pas supprimer votre propre compte"
        )

    # Désactiver au lieu de supprimer
    await prisma.user.update(
        where={"id": user_id},
        data={"is_active": not existing.is_active}
    )

    return {
        "message": "Utilisateur désactivé avec succès",
        "user_id": user_id
    }


# ============================================
# GESTION SÉMINARISTES (CRUD Admin)
# ============================================

@router.post("/seminaristes", response_model=SeminaristeResponse, status_code=201)
async def create_seminariste(
        data: SeminaristeCreate
):
    """Créer un séminariste manuellement"""

    # Vérifier que le dortoir existe
    dortoir = await prisma.dortoir.find_unique(where={"code": data.dortoir_code})
    if not dortoir:
        raise HTTPException(status_code=404, detail="Dortoir non trouvé")

    # Vérifier capacité
    if dortoir.current_count >= dortoir.capacity:
        raise HTTPException(status_code=409, detail="Dortoir complet")

    # Vérifier genre
    if dortoir.gender != data.sexe:
        raise HTTPException(
            status_code=400,
            detail=f"Ce dortoir est réservé au genre {dortoir.gender}"
        )

    # Générer matricule
    from app.utils.matricule_generator import generate_matricule
    matricule = await generate_matricule(data.dortoir_code)

    # Créer séminariste
    seminariste = await prisma.registration.create(
        data={
            **data.model_dump(),
            "matricule": matricule,
            "payment_status": "completed",
            "validated": True
        }
    )

    # Incrémenter compteur dortoir
    await prisma.dortoir.update(
        where={"code": data.dortoir_code},
        data={"current_count": {"increment": 1}}
    )

    # Récupérer avec relations
    result = await prisma.registration.find_unique(
        where={"id": seminariste.id},
        include={"dortoir": True}
    )

    return {
        **result.model_dump(),
        "dortoir_name": result.dortoir.name if result.dortoir else None
    }


@router.get("/seminaristes/{matricule}", response_model=SeminaristeResponse)
async def get_seminariste_detail(
        matricule: str
):
    """Détail complet d'un séminariste"""

    seminariste = await prisma.registration.find_unique(
        where={"matricule": matricule},
        include={"dortoir": True}
    )

    if not seminariste:
        raise HTTPException(status_code=404, detail="Séminariste non trouvé")

    return {
        **seminariste.model_dump(),
        "dortoir_name": seminariste.dortoir.name if seminariste.dortoir else None
    }


@router.put("/seminaristes/{matricule}", response_model=SeminaristeResponse)
async def update_seminariste(
        matricule: str,
        data: SeminaristeUpdate
):
    """Modifier un séminariste"""

    existing = await prisma.registration.find_unique(
        where={"matricule": matricule}
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Séminariste non trouvé")

    update_data = data.model_dump(exclude_unset=True)

    # Si changement de dortoir
    if "dortoir_code" in update_data and update_data["dortoir_code"] != existing.dortoir_code:
        # Vérifier nouveau dortoir
        new_dortoir = await prisma.dortoir.find_unique(
            where={"code": update_data["dortoir_code"]}
        )
        if not new_dortoir:
            raise HTTPException(status_code=404, detail="Nouveau dortoir non trouvé")

        if new_dortoir.current_count >= new_dortoir.capacity:
            raise HTTPException(status_code=409, detail="Nouveau dortoir complet")

        if new_dortoir.gender != existing.sexe:
            raise HTTPException(
                status_code=400,
                detail=f"Le nouveau dortoir est réservé au genre {new_dortoir.gender}"
            )

        # Décrémenter ancien dortoir
        await prisma.dortoir.update(
            where={"code": existing.dortoir_code},
            data={"current_count": {"decrement": 1}}
        )

        # Incrémenter nouveau dortoir
        await prisma.dortoir.update(
            where={"code": update_data["dortoir_code"]},
            data={"current_count": {"increment": 1}}
        )

    # Mettre à jour
    seminariste = await prisma.registration.update(
        where={"matricule": matricule},
        data=update_data,
        include={"dortoir": True}
    )

    return {
        **seminariste.model_dump(),
        "dortoir_name": seminariste.dortoir.name if seminariste.dortoir else None
    }


@router.delete("/seminaristes/{matricule}")
async def delete_seminariste(
        matricule: str
):
    """Supprimer un séminariste"""

    seminariste = await prisma.registration.find_unique(
        where={"matricule": matricule}
    )
    if not seminariste:
        raise HTTPException(status_code=404, detail="Séminariste non trouvé")

    # Décrémenter compteur dortoir
    await prisma.dortoir.update(
        where={"code": seminariste.dortoir_code},
        data={"current_count": {"decrement": 1}}
    )

    # Supprimer
    await prisma.registration.delete(where={"matricule": matricule})

    return {
        "message": "Séminariste supprimé avec succès",
        "matricule": matricule
    }

# ============================================
# AUTRES FONCTIONNALITÉS ADMIN
# ============================================
# api renvoyant la liste de tous les niveaux académiques, le code et le nom des dortoirs pour les formulaires
@router.get("/metadata")
async def get_metadata():
    """Récupérer les métadonnées pour les formulaires admin des données dans l'attribut niveaux_academiques et dortoirs des registations"""

    """Métadonnées admin"""

    niveaux_raw = await prisma.registration.find_many(
        distinct=["niveau_academique"]
    )

    niveaux = [
        r.niveau_academique
        for r in niveaux_raw
        if r.niveau_academique is not None
    ]

    dortoirs = await prisma.dortoir.find_many()

    return {
        "niveaux_academiques": niveaux,
        "dortoirs": [
            {"code": d.code, "name": d.name}
            for d in dortoirs
        ]
    }

@router.get("/static-metadata")
async def get_static_metadata():
    """Métadonnées statiques (niveaux académiques, communes CI, dortoirs)"""

    niveaux_academiques = {
        "Primaire": ["CP1", "CP2", "CE1", "CE2", "CM1", "CM2"],
        "Collège": ["6ème", "5ème", "4ème", "3ème"],
        "Lycée": ["2nde", "1ère", "Terminale"],
        "Licence": ["Licence 1", "Licence 2", "Licence 3"],
        "Master": ["Master 1", "Master 2"],
        "Ingenieur": ["Ingenieur 1", "Ingenieur 2", "Ingenieur 3"],
        "Bts": ["Bts 1", "Bts 2"],
        "Doctorat": ["Doctorat 1", "Doctorat 2", "Doctorat 3"],
        "Professionnel": ["Professionnel"]
    }

    communes_ci = [
        "Abobo", "Adjamé", "Attécoubé", "Cocody", "Koumassi", "Marcory",
        "Plateau", "Port-Bouët", "Treichville", "Yopougon", "Bingerville",
        "Songon", "Anyama", "Bouaké", "Daloa", "Korhogo", "San-Pédro",
        "Yamoussoukro", "Man", "Gagnoa", "Divo", "Abengourou", "Agboville",
        "Grand-Bassam", "Autre"
    ]

    dortoirs = {
        "Masculin": [
            {"code": "NASSR", "name": "Nassr – Victoire"},
            {"code": "BASIR", "name": "Basîr – Clairvoyance"},
            {"code": "HILM", "name": "Hilm – Maîtrise de soi"},
            {"code": "SIDANE", "name": "Sidane – Gardien"},
            {"code": "FURQAN", "name": "Furqân – Discernement"},
            {"code": "RIYADH", "name": "Riyâdh – Jardins"}
        ],
        "Féminin": [
            {"code": "NAJMA", "name": "Najma – Étoile"},
            {"code": "HIDAYA", "name": "Hidaya – Guidance"},
            {"code": "RAHMA", "name": "Rahma – Miséricorde"},
            {"code": "SAKINA", "name": "Sakîna – Sérénité"},
            {"code": "SALWA", "name": "Salwa – Réconfort"},
            {"code": "ZAHRA", "name": "Zahra – Fleur / Pureté"},
            {"code": "FIRDAOUS", "name": "Firdaous"},
            {"code": "SALAM", "name": "Salam"}
        ],
        "Pépinière": [
            {"code": "PEPINIERE-G", "name": "Pépinière – Garçons"},
            {"code": "PEPINIERE-F", "name": "Pépinière – Filles"}
        ]
    }

    return {
        "niveaux_academiques": niveaux_academiques,
        "communes": communes_ci,
        "dortoirs": dortoirs
    }