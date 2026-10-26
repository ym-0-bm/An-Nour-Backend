from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import JSONResponse
from typing import Optional, List
from datetime import datetime
import cloudinary.uploader
import os
import json
import logging
from bson import ObjectId
from bson.errors import InvalidId

from app.utils.auth import RequireAdmin, RequireLectureSeminaristes
from app.utils.rate_limit import rate_limit
from app.utils.uploads import read_image_upload

from app.models.schemas import (
    RegistrationCreate, RegistrationUpdate, RegistrationResponse,
    PaginatedResponse, DortoirResponse
)
from app.database import prisma
from app.utils.matricule_generator import generate_matricule
from app.utils.ocr_processor import OCRProcessor
from app.utils.receipt_analyzer import ReceiptAnalyzer
from app.config import settings


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
)
logger = logging.getLogger(__name__)

ocr_processor = OCRProcessor()
receipt_analyzer = ReceiptAnalyzer()

router = APIRouter(prefix="/registrations", tags=["Registrations"])


@router.post("/verify-receipt", dependencies=[Depends(rate_limit("verify-receipt", 10, 60))])
async def verify_receipt(file: UploadFile = File(...)):
    # Tarif unique fixé côté serveur : un éventuel ?expected_amount= du client est ignoré
    expected_amount = settings.REGISTRATION_FEE
    contents = await read_image_upload(file, settings.MAX_UPLOAD_BYTES)
    logger.info(f"📸 Reçu de {len(contents)} octets | 💰 {expected_amount} FCFA")

    # OCR (prétraitement obligatoire à l'intérieur de process_image)
    # Une erreur inattendue est journalisée et masquée par le gestionnaire global
    text = ocr_processor.process_image(contents)

    # Analyse
    result = await receipt_analyzer.analyze_receipt(text, expected_amount)

    return JSONResponse(content=result, status_code=200)



# CREATE - Créer une inscription (avec le JSON du front)
@router.post("", response_model=RegistrationResponse, status_code=201,
             dependencies=[Depends(rate_limit("registrations", 20, 60))])
async def create_registration(data: RegistrationCreate):
    """Créer une nouvelle inscription à partir du JSON du front"""

    # Extraire les données
    personal = data.personalInfo
    dormitory = data.dormitoryInfo
    health = data.healthInfo
    payment = data.paymentInfo

    # print les données reçues
    # print("Données reçues pour l'inscription :")
    # print(data.model_dump())

    # Vérifier que le dortoir existe
    dortoir = await prisma.dortoir.find_unique(where={"code": dormitory.dortoirId})
    if not dortoir:
        raise HTTPException(status_code=404, detail="Dortoir non trouvé")

    # Vérifier la capacité
    if dortoir.current_count >= dortoir.capacity:
        raise HTTPException(status_code=409, detail="Le dortoir sélectionné est complet")

    # Vérifier le genre
    if dortoir.gender != personal.sexe:
        raise HTTPException(
            status_code=400,
            detail=f"Ce dortoir est réservé au genre {dortoir.gender}"
        )

    # Générer le matricule
    matricule = await generate_matricule(dormitory.dortoirId)

    # Vérifier que la transactionId est unique dans la base de données
    transaction_id = await prisma.registration.find_first(
    where={"transaction_id": payment.transactionId}
    )
    if transaction_id:
        raise HTTPException(
            status_code=409,
            detail="L'ID de transaction fourni a déjà été utilisée pour une autre inscription"
        )

    """# Générer la chaîne QR code (JSON stringifié)
    qr_data = json.dumps({
        "matricule": matricule,
        "nom": personal.nom,
        "prenom": personal.prenom,
        "dortoir": dormitory.dortoir,
        "transaction_id": payment.transactionId
    })"""

    # Créer l'inscription (sans le montant)
    registration = await prisma.registration.create(
        data={
            "matricule": matricule,
            "nom": personal.nom.upper(),
            "prenom": personal.prenom.upper(),
            "sexe": personal.sexe,
            "age": int(personal.age),
            "niveau_academique": personal.niveauAcademique,
            # Gère commune_habitation avec fallback sur communeAutre
            "commune_habitation": personal.communeHabitation if personal.communeHabitation else personal.communeAutre,
            "contact_parent": personal.contactParent,
            "contact_seminariste": personal.contactSeminariste,
            "dortoir_code": dormitory.dortoirId,
            "allergie": health.allergie or "RAS",
            "antecedent_medical": health.antecedentMedical or "Néant",
            "payment_status": "completed",
            "transaction_id": payment.transactionId,
            "receipt_url": payment.receiptUrl,
            #"qr_code_data": qr_data,
        }
    )

    # Mettre à jour le compteur du dortoir
    await prisma.dortoir.update(
        where={"code": dormitory.dortoirId},
        data={"current_count": {"increment": 1}}
    )

    # Créer automatiquement l'entrée finance (non-bloquant)
    try:
        from app.utils.finance_utils import create_inscription_entry
        await create_inscription_entry(
            matricule=matricule,
            nom=personal.nom.upper(),
            prenom=personal.prenom.upper(),
            montant=float(settings.REGISTRATION_FEE),  # tarif unique, jamais le montant du client
            registration_date=datetime.now()
        )
    except Exception as e:
        # Log l'erreur mais ne bloque pas l'inscription
        logger.warning(f"Erreur création entrée finance pour {matricule}: {e}")

    # Récupérer avec les relations
    result = await prisma.registration.find_unique(
        where={"id": registration.id},
        include={"dortoir": True}
    )

    # Retourner toutes les infos vers le front
    return RegistrationResponse(
        id=result.id,
        matricule=result.matricule,
        nom=result.nom,
        prenom=result.prenom,
        sexe=result.sexe,
        age=result.age,
        commune_habitation=result.commune_habitation,
        contact_parent=result.contact_parent,
        contact_seminariste=result.contact_seminariste,
        niveau_academique=result.niveau_academique,
        dortoir_code=result.dortoir_code,
        dortoir_name=result.dortoir.name if result.dortoir else None,
        allergie=result.allergie,
        antecedent_medical=result.antecedent_medical,
        payment_status=result.payment_status,
        transaction_id=result.transaction_id,
        # qr_code_data=result.qr_code_data,
        photo_url=result.photo_url,
        validated=result.validated,
        registration_date=result.registration_date
    )


# GET DORTOIRS - Récupérer tous les dortoirs avec places disponibles sans les dortoirs de pépinière
@router.get("/dortoirs", response_model=List[DortoirResponse])
async def get_dortoirs(sexe: Optional[str] = Query(None, description="Filtrer par sexe: M ou F")):
    """Récupérer tous les dortoirs avec le nombre de places disponibles, filtrés par sexe si spécifié sans les dortoirs de pépinière"""

    # Construire le filtre en fonction du sexe
    where = {}
    if sexe:
        if sexe not in ["M", "F"]:
            raise HTTPException(
                status_code=400,
                detail="Le paramètre 'sexe' doit être 'M' ou 'F'"
            )
        where["gender"] = sexe

    # Exclure le dortoir pepinière correspondant
    pepiniere_code = f"PEPINIERE-{'G' if sexe == 'M' else 'F'}"
    where["code"] = {"not": pepiniere_code}

    # Récupérer les dortoirs (sans orderBy qui pose un problème avec MongoDB)
    dortoirs = await prisma.dortoir.find_many(where=where)

    # Trier en Python
    dortoirs_sorted = sorted(dortoirs, key=lambda d: (d.gender, d.code))

    return [
        DortoirResponse(
            code=d.code,
            name=d.name,
            capacity=d.capacity,
            current_count=d.current_count,
            available=d.capacity - d.current_count,
            gender=d.gender
        )
        for d in dortoirs_sorted
    ]

# GET ALL DORTOIRS - Récupérer tous les dortoirs avec places disponibles
@router.get("/alldortoirs", response_model=List[DortoirResponse])
async def get_all_dortoirs(sexe: Optional[str] = Query(None, description="Filtrer par sexe: M ou F")):
    """Récupérer tous les dortoirs avec le nombre de places disponibles, filtrés par sexe si spécifié"""

    # Construire le filtre
    where = {}
    if sexe:
        if sexe not in ["M", "F"]:
            raise HTTPException(
                status_code=400,
                detail="Le paramètre 'sexe' doit être 'M' ou 'F'"
            )
        where["gender"] = sexe

    # Récupérer les dortoirs (sans orderBy qui pose un problème avec MongoDB)
    dortoirs = await prisma.dortoir.find_many(where=where)

    # Trier en Python
    dortoirs_sorted = sorted(dortoirs, key=lambda d: (d.gender, d.code))

    return [
        DortoirResponse(
            code=d.code,
            name=d.name,
            capacity=d.capacity,
            current_count=d.current_count,
            available=d.capacity - d.current_count,
            gender=d.gender
        )
        for d in dortoirs_sorted
    ]

# READ ALL - Récupérer toutes les inscriptions
@router.get("", response_model=PaginatedResponse, dependencies=[RequireLectureSeminaristes])
async def get_registrations(
        page: int = Query(1, ge=1),
        limit: int = Query(20, ge=1, le=100),
        sexe: Optional[str] = None,
        dortoir: Optional[str] = None,
        payment_status: Optional[str] = None,
        search: Optional[str] = None
):
    """Récupérer toutes les inscriptions avec filtres"""

    # Construire les filtres
    where = {}
    if sexe:
        where["sexe"] = sexe
    if dortoir:
        where["dortoir_code"] = dortoir
    if payment_status:
        where["payment_status"] = payment_status
    if search:
        where["OR"] = [
            {"nom": {"contains": search, "mode": "insensitive"}},
            {"prenom": {"contains": search, "mode": "insensitive"}},
            {"matricule": {"contains": search, "mode": "insensitive"}},
        ]

    # Compter le total
    total = await prisma.registration.count(where=where)

    # Récupérer les données
    skip = (page - 1) * limit
    registrations = await prisma.registration.find_many(
        where=where,
        skip=skip,
        take=limit,
        include={"dortoir": True}
    )

    # Trier par date (plus récent en premier) en Python
    registrations_sorted = sorted(registrations, key=lambda r: r.registration_date, reverse=True)

    # Formater les résultats
    data = [
        RegistrationResponse(
            id=reg.id,
            matricule=reg.matricule,
            nom=reg.nom,
            prenom=reg.prenom,
            sexe=reg.sexe,
            age=reg.age,
            contact_parent=reg.contact_parent,
            contact_seminariste=reg.contact_seminariste,
            niveau_academique=reg.niveau_academique,
            dortoir_code=reg.dortoir_code,
            dortoir_name=reg.dortoir.name if reg.dortoir else None,
            allergie=reg.allergie,
            antecedent_medical=reg.antecedent_medical,
            payment_status=reg.payment_status,
            transaction_id=reg.transaction_id,
            # qr_code_data=reg.qr_code_data,
            photo_url=reg.photo_url,
            validated=reg.validated,
            registration_date=reg.registration_date,
            commune_habitation=reg.commune_habitation
        )
        for reg in registrations_sorted
    ]

    return PaginatedResponse(
        total=total,
        page=page,
        limit=limit,
        data=data
    )


# READ ONE - Récupérer une inscription
@router.get("/{identifier}", response_model=RegistrationResponse, dependencies=[RequireLectureSeminaristes])
async def get_registration(identifier: str):

    """
    Récupérer une inscription par ID (ObjectId MongoDB) ou matricule
    ✅ Gère correctement la distinction entre ObjectId et matricule
    """

    registration = None

    # Essayer d'abord de déterminer si c'est un ObjectId valide
    try:
        # Si ça ressemble à un ObjectId (24 caractères hexadécimaux)
        if len(identifier) == 24:
            ObjectId(identifier)  # Validation
            # C'est probablement un ID, chercher par ID
            registration = await prisma.registration.find_unique(
                where={"id": identifier},
                include={"dortoir": True}
            )
    except (InvalidId, Exception):
        # Pas un ObjectId valide, c'est donc un matricule
        pass

    # Si pas trouvé par ID ou si ce n'était pas un ID, chercher par matricule
    if not registration:
        registration = await prisma.registration.find_unique(
            where={"matricule": identifier},
            include={"dortoir": True}
        )

    if not registration:
        raise HTTPException(
            status_code=404,
            detail=f"Inscription non trouvée pour l'identifiant: {identifier}"
        )

    return RegistrationResponse(
        id=registration.id,
        matricule=registration.matricule,
        nom=registration.nom,
        prenom=registration.prenom,
        sexe=registration.sexe,
        age=registration.age,
        commune_habitation=registration.commune_habitation,
        contact_parent=registration.contact_parent,
        contact_seminariste=registration.contact_seminariste,
        niveau_academique=registration.niveau_academique,
        dortoir_code=registration.dortoir_code,
        dortoir_name=registration.dortoir.name if registration.dortoir else None,
        allergie=registration.allergie,
        antecedent_medical=registration.antecedent_medical,
        payment_status=registration.payment_status,
        transaction_id=registration.transaction_id,
        # qr_code_data=registration.qr_code_data,
        photo_url=registration.photo_url,
        validated=registration.validated,
        registration_date=registration.registration_date
    )

# UPDATE - Mettre à jour une inscription
@router.put("/{id}", response_model=dict, dependencies=[RequireAdmin])
async def update_registration(id: str, data: RegistrationUpdate):
    """Mettre à jour une inscription"""

    existing = await prisma.registration.find_unique(where={"id": id})
    if not existing:
        raise HTTPException(status_code=404, detail="Inscription non trouvée")

    update_data = data.model_dump(exclude_unset=True)

    # Si paiement complété, ajouter le timestamp
    if update_data.get("payment_status") == "completed":
        update_data["payment_timestamp"] = datetime.now()

    updated = await prisma.registration.update(
        where={"id": id},
        data=update_data
    )

    return {
        "id": updated.id,
        "matricule": updated.matricule,
        "message": "Inscription mise à jour avec succès",
        "updated_fields": list(update_data.keys())
    }


# UPDATE PHOTO - Ajouter une photo
@router.post("/{matricule}/photo", dependencies=[RequireAdmin])
async def upload_photo(matricule: str, photo: UploadFile = File(...)):
    """Ajouter/modifier la photo du participant"""

    # Vérifier que l'inscription existe
    registration = await prisma.registration.find_unique(
        where={"matricule": matricule}
    )
    if not registration:
        raise HTTPException(status_code=404, detail="Inscription non trouvée")

    # Créer le dossier s'il n'existe pas (ancien code local - désactivé)
    # photo_dir = os.path.join(settings.MEDIA_DIR, "participants_photos")
    # os.makedirs(photo_dir, exist_ok=True)

    # Sauvegarder la photo (ancien code local - désactivé)
    # extension = photo.filename.split(".")[-1]
    # filename = f"{matricule}.{extension}"
    # filepath = os.path.join(photo_dir, filename)

    # with open(filepath, "wb") as f:
    #     content = await photo.read()
    #     f.write(content)

    # Lire le contenu du fichier (taille bornée, contenu vérifié)
    file_bytes = await read_image_upload(photo, settings.MAX_UPLOAD_BYTES)

    # Envoyer vers Cloudinary
    try:
        upload_result = cloudinary.uploader.upload(
            file_bytes,
            folder="participants_photos",  # dossier dans cloudinary
            public_id=matricule,  # nom du fichier
            overwrite=True,  # remplace ancienne photo
            resource_type="image"
        )
    except Exception:
        logger.exception("Échec de l'envoi de la photo vers Cloudinary")
        raise HTTPException(status_code=502, detail="Service de stockage des photos indisponible")

    # Ancien code local - désactivé
    # photo_url = f"/media/participants_photos/{filename}"
    # await prisma.registration.update(
    #     where={"matricule": matricule},
    #     data={
    #         "photo_url": photo_url,
    #         "validated": True
    #     }
    # )
    # return {
    #     "matricule": matricule,
    #     "photo_url": photo_url,
    #     "validated": True,
    #     "message": "Photo ajoutée avec succès"
    # }

    # Récupérer l'URL sécurisée
    image_url = upload_result.get("secure_url")

    # Mettre à jour la BDD
    await prisma.registration.update(
        where={"matricule": matricule},
        data={
            "photo_url": image_url,
            "validated": True
        }
    )


# DELETE - Supprimer une inscription
@router.delete("/{id}", dependencies=[RequireAdmin])
async def delete_registration(id: str):
    """Supprimer une inscription (admin uniquement)"""

    registration = await prisma.registration.find_unique(where={"id": id})
    if not registration:
        raise HTTPException(status_code=404, detail="Inscription non trouvée")

    # Décrémenter le compteur du dortoir
    await prisma.dortoir.update(
        where={"code": registration.dortoir_code},
        data={"current_count": {"decrement": 1}}
    )

    # Supprimer l'inscription
    await prisma.registration.delete(where={"id": id})

    return {
        "message": "Inscription supprimée avec succès",
        "deleted_id": id,
        "matricule": registration.matricule
    }


# STATISTICS - Statistiques globales
@router.get("/stats/global", dependencies=[RequireLectureSeminaristes])
async def get_statistics():
    """Obtenir les statistiques globales"""

    total = await prisma.registration.count()
    completed = await prisma.registration.count(where={"payment_status": "completed"})
    pending = await prisma.registration.count(where={"payment_status": "pending"})
    awaiting = await prisma.registration.count(where={"payment_status": "awaiting_proof"})

    males = await prisma.registration.count(where={"sexe": "M"})
    females = await prisma.registration.count(where={"sexe": "F"})

    # Par dortoir
    dortoirs = await prisma.dortoir.find_many()
    by_dortoir = {}
    for dortoir in dortoirs:
        count = await prisma.registration.count(where={"dortoir_code": dortoir.code})
        by_dortoir[dortoir.name] = {
            "code": dortoir.code,
            "count": count,
            "capacity": dortoir.capacity,
            "available": dortoir.capacity - count
        }

    # Revenue (6000 FCFA par inscription complétée)
    total_revenue = completed * 6000

    return {
        "total_registrations": total,
        "payment_stats": {
            "completed": completed,
            "pending": pending,
            "awaiting_proof": awaiting
        },
        "by_gender": {
            "males": males,
            "females": females
        },
        "by_dortoir": by_dortoir,
        "revenue": {
            "total": total_revenue,
            "completed_payments": completed,
            "currency": "FCFA"
        }
    }