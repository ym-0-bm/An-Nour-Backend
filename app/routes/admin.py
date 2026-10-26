from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Depends, Response
from openpyxl import Workbook
from typing import Optional, List
from datetime import datetime, timedelta
import io
# import pandas as pd

from app.models.admin_schemas import (
    UserLogin, UserCreate, UserUpdate, UserResponse, Token,
    SeminaristeCreate, SeminaristeUpdate, SeminaristeResponse,
    DashboardStats, ImportResult, ExportParams,
    BadgeGenerate, DiplomeGenerate, ListePDFGenerate,
    MembreCOCreate, MembreCOUpdate, MembreCOResponse, MembreCOListResponse
)
from app.database import prisma
from app.utils.pagination import Page, set_total
from app.utils.rate_limit import limiter, rate_limit, too_many_requests
from app.utils.auth import (
    get_password_hash, verify_password, create_access_token,
    get_current_user, RequireAdmin, RequireAny, RequireLectureSeminaristes, ROLE_ADMIN
)

router = APIRouter(prefix="/admin", tags=["Administration"])


# ============================================
# AUTHENTIFICATION
# ============================================

@router.post("/auth/register", response_model=UserResponse, status_code=201, dependencies=[RequireAdmin])
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


# Échecs de connexion tolérés par identifiant avant blocage temporaire
LOGIN_MAX_FAILURES = 5
LOGIN_FAILURE_WINDOW = 15 * 60
# Hash factice : un identifiant inconnu coûte le même temps de vérification qu'un mot de passe faux
_DUMMY_PASSWORD_HASH = get_password_hash("mot-de-passe-factice-anti-enumeration")


@router.post("/auth/login", response_model=Token, dependencies=[Depends(rate_limit("login", 10, 60))])
async def login(data: UserLogin):
    """Connexion avec email/password"""

    # Limite par identifiant tenté (quelle que soit l'IP), en plus de la limite par IP
    failure_key = f"login-failures:{data.identifier.strip().lower()}"
    wait = limiter.retry_after(failure_key, LOGIN_MAX_FAILURES, LOGIN_FAILURE_WINDOW)
    if wait is not None:
        raise too_many_requests(wait)

    # Chercher utilisateur email ou username

    user = await prisma.user.find_first(
        where={
            "OR": [
                {"email": data.identifier},
                {"username": data.identifier}
            ]
        }
    )

    # Vérifier mot de passe (même réponse si l'utilisateur n'existe pas)
    password_ok = verify_password(data.password, user.password if user else _DUMMY_PASSWORD_HASH)
    if not user or not password_ok:
        limiter.add(failure_key)
        raise HTTPException(
            status_code=401,
            detail="Email ou mot de passe incorrect"
        )

    limiter.reset(failure_key)

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

@router.get("/users", response_model=List[UserResponse], dependencies=[RequireAdmin])
async def get_users(
        response: Response,
        role: Optional[str] = None,
        is_active: Optional[bool] = None,
        page: Page = Depends()
):
    """Liste des utilisateurs"""

    where = {}
    if role:
        where["role"] = role
    if is_active is not None:
        where["is_active"] = is_active

    set_total(response, await prisma.user.count(where=where))
    return await prisma.user.find_many(
        where=where, order=[{"nom": "asc"}, {"prenom": "asc"}], skip=page.skip, take=page.limit
    )


@router.get("/users/{user_id}", response_model=UserResponse, dependencies=[RequireAdmin])
async def get_user(user_id: str):
    """Détail d'un utilisateur"""

    user = await prisma.user.find_unique(where={"id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

    return user


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
        user_id: str,
        data: UserUpdate,
        current_user=RequireAdmin
):
    """Modifier un utilisateur"""

    existing = await prisma.user.find_unique(where={"id": user_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

    update_data = data.model_dump(exclude_unset=True)

    # Unicité de l'email et du nom d'utilisateur
    for field, label in (("email", "Cet email est déjà utilisé"), ("username", "Ce nom d'utilisateur est déjà pris")):
        if field in update_data and update_data[field] != getattr(existing, field):
            other = await prisma.user.find_unique(where={field: update_data[field]})
            if other and other.id != user_id:
                raise HTTPException(status_code=409, detail=label)

    # Un admin ne peut ni se désactiver ni se retirer son propre rôle
    if user_id == current_user.id and (
        update_data.get("is_active") is False
        or update_data.get("role", ROLE_ADMIN) != ROLE_ADMIN
    ):
        raise HTTPException(
            status_code=400,
            detail="Vous ne pouvez pas désactiver ou rétrograder votre propre compte"
        )

    # Si mot de passe, le hasher
    if "password" in update_data:
        update_data["password"] = get_password_hash(update_data["password"])

    user = await prisma.user.update(
        where={"id": user_id},
        data=update_data
    )

    return user


@router.patch("/users/{user_id}")
async def delete_user(user_id: str, current_user=RequireAdmin):
    """Activer / désactiver un utilisateur (bascule)"""

    existing = await prisma.user.find_unique(where={"id": user_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

    # Ne pas désactiver soi-même
    if user_id == current_user.id:
        raise HTTPException(
            status_code=400,
            detail="Vous ne pouvez pas désactiver votre propre compte"
        )

    # Désactiver au lieu de supprimer
    is_active = not existing.is_active
    await prisma.user.update(
        where={"id": user_id},
        data={"is_active": is_active}
    )

    return {
        "message": "Utilisateur activé avec succès" if is_active else "Utilisateur désactivé avec succès",
        "user_id": user_id,
        "is_active": is_active
    }


# ============================================
# GESTION SÉMINARISTES (CRUD Admin)
# ============================================

@router.post("/seminaristes", response_model=SeminaristeResponse, status_code=201, dependencies=[RequireAdmin])
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


@router.get("/seminaristes/{matricule}", response_model=SeminaristeResponse, dependencies=[RequireLectureSeminaristes])
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


@router.put("/seminaristes/{matricule}", response_model=SeminaristeResponse, dependencies=[RequireAdmin])
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


@router.delete("/seminaristes/{matricule}", dependencies=[RequireAdmin])
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
# ============================================
# EXPORT EXCEL DES SÉMINARISTES (B12)
# ============================================

EXPORT_COLUMNS = [
    ("Matricule", "matricule"), ("Nom", "nom"), ("Prénom", "prenom"), ("Sexe", "sexe"), ("Âge", "age"),
    ("Niveau académique", "niveau_academique"), ("Niveau du séminaire", "niveau"), ("Dortoir", "dortoir"),
    ("Commune", "commune_habitation"), ("Contact parent", "contact_parent"),
    ("Contact séminariste", "contact_seminariste"), ("Statut du paiement", "payment_status"),
    ("Date d'inscription", "registration_date"),
]


def _excel_safe(value):
    """Neutralise l'injection de formules : une cellule commençant par = + - @ serait exécutée par Excel."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


@router.get("/export/seminaristes.xlsx", dependencies=[RequireAdmin])
async def export_seminaristes_excel():
    """Export Excel des séminaristes (admin). Les données médicales ne sont pas exportées."""
    registrations = await prisma.registration.find_many(
        include={"dortoir": True, "seminaristes": True},
        order={"matricule": "asc"}
    )

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Séminaristes"
    sheet.append([titre for titre, _ in EXPORT_COLUMNS])
    for r in registrations:
        valeurs = {
            **r.model_dump(),
            "niveau": r.seminaristes[0].niveau if r.seminaristes else None,
            "dortoir": r.dortoir.name if r.dortoir else r.dortoir_code,
            "payment_status": "verifie_historique" if r.payment_status == "completed" else r.payment_status,
            "registration_date": r.registration_date.replace(tzinfo=None) if r.registration_date else None,
        }
        sheet.append([_excel_safe(valeurs.get(champ)) for _, champ in EXPORT_COLUMNS])

    buffer = io.BytesIO()
    workbook.save(buffer)
    nom_fichier = f"seminaristes_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nom_fichier}"'},
    )


@router.get("/metadata", dependencies=[RequireAny])
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


# ============================================
# GESTION MEMBRES CO (Comité d'Organisation)
# ============================================

@router.post("/membres-co", response_model=MembreCOResponse, status_code=201, dependencies=[RequireAdmin])
async def create_membre_co(data: MembreCOCreate):
    """
    Créer un nouveau membre du Comité d'Organisation
    
    - **nom**: Nom du membre
    - **prenoms**: Prénoms du membre
    - **contact**: Numéro de téléphone ou email
    - **commission**: Commission assignée (Logistique, Communication, Scientifique, etc.)
    - **statut**: Statut du membre (Actif, Inactif, Responsable)
    - **photo_url**: URL de la photo (optionnel)
    - **allergies**: Allergies connues (optionnel, défaut: RAS)
    - **antecedent_medical**: Antécédents médicaux (optionnel, défaut: Néant)
    """
    
    # Créer le membre CO dans la base de données
    membre = await prisma.membreco.create(
        data={
            "nom": data.nom.upper(),
            "prenoms": data.prenoms.title(),
            "contact": data.contact,
            "commission": data.commission,
            "statut": data.statut,
            "photo_url": data.photo_url,
            "allergies": data.allergies or "RAS",
            "antecedent_medical": data.antecedent_medical or "Néant"
        }
    )
    
    return MembreCOResponse(
        id=membre.id,
        nom=membre.nom,
        prenoms=membre.prenoms,
        contact=membre.contact,
        commission=membre.commission,
        statut=membre.statut,
        photo_url=membre.photo_url,
        allergies=membre.allergies,
        antecedent_medical=membre.antecedent_medical,
        created_at=membre.created_at
    )


@router.get("/membres-co", response_model=MembreCOListResponse, dependencies=[RequireAdmin])
async def get_membres_co(
    response: Response,
    commission: Optional[str] = None,
    statut: Optional[str] = None,
    page: Page = Depends()
):
    """
    Récupérer la liste de tous les membres du CO
    
    - **commission**: Filtrer par commission (optionnel)
    - **statut**: Filtrer par statut (optionnel)
    """
    
    # Construire le filtre
    where = {}
    if commission:
        where["commission"] = commission
    if statut:
        where["statut"] = statut
    
    # Récupérer tous les membres du CO
    total = await prisma.membreco.count(where=where)
    set_total(response, total)
    membres = await prisma.membreco.find_many(
        where=where, order=[{"commission": "asc"}, {"nom": "asc"}, {"prenoms": "asc"}],
        skip=page.skip, take=page.limit
    )
    
    # Trier par commission puis nom
    membres_sorted = membres
    
    # Formater la réponse
    data = [
        MembreCOResponse(
            id=m.id,
            nom=m.nom,
            prenoms=m.prenoms,
            contact=m.contact,
            commission=m.commission,
            statut=m.statut,
            photo_url=m.photo_url,
            allergies=m.allergies,
            antecedent_medical=m.antecedent_medical,
            created_at=m.created_at
        )
        for m in membres_sorted
    ]
    
    return MembreCOListResponse(
        total=total,
        data=data
    )


@router.get("/membres-co/{membre_id}", response_model=MembreCOResponse, dependencies=[RequireAdmin])
async def get_membre_co(membre_id: str):
    """
    Récupérer un membre du CO par son ID
    """
    
    membre = await prisma.membreco.find_unique(where={"id": membre_id})
    
    if not membre:
        raise HTTPException(status_code=404, detail="Membre du CO non trouvé")
    
    return MembreCOResponse(
        id=membre.id,
        nom=membre.nom,
        prenoms=membre.prenoms,
        contact=membre.contact,
        commission=membre.commission,
        statut=membre.statut,
        photo_url=membre.photo_url,
        allergies=membre.allergies,
        antecedent_medical=membre.antecedent_medical,
        created_at=membre.created_at
    )


@router.put("/membres-co/{membre_id}", response_model=MembreCOResponse, dependencies=[RequireAdmin])
async def update_membre_co(membre_id: str, data: MembreCOUpdate):
    """
    Mettre à jour un membre du CO
    """
    
    existing = await prisma.membreco.find_unique(where={"id": membre_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Membre du CO non trouvé")
    
    update_data = data.model_dump(exclude_unset=True)
    
    # Formater les noms si fournis
    if "nom" in update_data:
        update_data["nom"] = update_data["nom"].upper()
    if "prenoms" in update_data:
        update_data["prenoms"] = update_data["prenoms"].title()
    
    membre = await prisma.membreco.update(
        where={"id": membre_id},
        data=update_data
    )
    
    return MembreCOResponse(
        id=membre.id,
        nom=membre.nom,
        prenoms=membre.prenoms,
        contact=membre.contact,
        commission=membre.commission,
        statut=membre.statut,
        photo_url=membre.photo_url,
        allergies=membre.allergies,
        antecedent_medical=membre.antecedent_medical,
        created_at=membre.created_at
    )


@router.delete("/membres-co/{membre_id}", dependencies=[RequireAdmin])
async def delete_membre_co(membre_id: str):
    """
    Supprimer un membre du CO
    """
    
    membre = await prisma.membreco.find_unique(where={"id": membre_id})
    
    if not membre:
        raise HTTPException(status_code=404, detail="Membre du CO non trouvé")
    
    await prisma.membreco.delete(where={"id": membre_id})
    
    return {
        "message": "Membre du CO supprimé avec succès",
        "deleted_id": membre_id,
        "nom": membre.nom,
        "prenoms": membre.prenoms
    }