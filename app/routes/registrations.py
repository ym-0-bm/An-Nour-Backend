from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from fastapi.responses import JSONResponse
from typing import Optional, List
from datetime import datetime
import os
import json
import logging
from bson import ObjectId
from bson.errors import InvalidId

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

# Initialiser les services OCR
logger.info("🚀 Initialisation du serveur OCR...")
try:
    ocr_processor = OCRProcessor()  # ✅ Maintenant c'est une classe
    receipt_analyzer = ReceiptAnalyzer()  # ✅ Maintenant c'est une classe
    logger.info("✅ Serveur prêt!\n")
except Exception as e:
    logger.error(f"❌ Erreur initialisation:{e}")
    raise

router = APIRouter(prefix="/registrations", tags=["Registrations"])


@router.post("/verify-receipt")
async def verify_receipt(
        file: UploadFile = File(...),
        expected_amount: float = Query(7000, description="Montant attendu en FCFA")
):
    """Vérifie un reçu Wave avec Tesseract OCR"""
    if not file.content_type.startswith("image/"):
        raise HTTPException(400, "Le fichier doit être une image")

    try:
        logger.info(f"\n{'=' * 60}")
        logger.info(f"📸 {file.filename} | 💰 {expected_amount} FCFA")
        logger.info(f"{'=' * 60}")

        contents = await file.read()


        # OCR
        text = ocr_processor.process_image(contents)

        # Analyse
        result = await receipt_analyzer.analyze_receipt(text, expected_amount)

        return JSONResponse(content=result, status_code=200)

    except Exception as e:
        logger.error(f"❌ Erreur: {str(e)}", exc_info=True)
        raise HTTPException(500, f"Erreur serveur: {str(e)}")



# CREATE - Créer une inscription (avec le JSON du front)
@router.post("", response_model=RegistrationResponse, status_code=201)
async def create_registration(data: RegistrationCreate):
    """Créer une nouvelle inscription à partir du JSON du front"""

    # Extraire les données
    personal = data.personalInfo
    dormitory = data.dormitoryInfo
    health = data.healthInfo
    payment = data.paymentInfo

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

    # Vérifier que la transctionId est unique dans la base de données
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
    print("dortoir ID :",dormitory.dortoirId)
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


# GET ALL DORTOIRS - Récupérer tous les dortoirs avec places disponibles
@router.get("/dortoirs", response_model=List[DortoirResponse])
async def get_dortoirs(sexe: Optional[str] = Query(None, description="Filtrer par sexe: M ou F")):
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

    # Récupérer les dortoirs (sans orderBy qui pose problème avec MongoDB)
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
@router.get("", response_model=PaginatedResponse)
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
            commune_habitation=reg.commune_habitation,
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
@router.get("/{identifier}", response_model=RegistrationResponse)
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
@router.put("/{id}", response_model=dict)
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
@router.post("/{matricule}/photo")
async def upload_photo(matricule: str, photo: UploadFile = File(...)):
    """Ajouter/modifier la photo du participant"""

    registration = await prisma.registration.find_unique(
        where={"matricule": matricule}
    )
    if not registration:
        raise HTTPException(status_code=404, detail="Inscription non trouvée")

    # Créer le dossier s'il n'existe pas
    photo_dir = os.path.join(settings.MEDIA_DIR, "participants_photos")
    os.makedirs(photo_dir, exist_ok=True)

    # Sauvegarder la photo
    extension = photo.filename.split(".")[-1]
    filename = f"{matricule}.{extension}"
    filepath = os.path.join(photo_dir, filename)

    with open(filepath, "wb") as f:
        content = await photo.read()
        f.write(content)

    # Mettre à jour l'inscription
    photo_url = f"/media/participants_photos/{filename}"
    await prisma.registration.update(
        where={"matricule": matricule},
        data={
            "photo_url": photo_url,
            "validated": True
        }
    )

    return {
        "matricule": matricule,
        "photo_url": photo_url,
        "validated": True,
        "message": "Photo ajoutée avec succès"
    }


# DELETE - Supprimer une inscription
@router.delete("/{id}")
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
@router.get("/stats/global")
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

    # Revenue (7000 FCFA par inscription complétée)
    total_revenue = completed * 7000

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