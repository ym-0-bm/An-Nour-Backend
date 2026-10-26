from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, File
from fastapi.responses import JSONResponse
from typing import Optional, List
from datetime import datetime, timezone
import cloudinary.uploader
import os
import json
import hashlib
import hmac
import logging
import secrets
from bson import ObjectId
from bson.errors import InvalidId

from prisma.errors import UniqueViolationError

from app.utils.auth import RequireAdmin, RequireFinances, RequireLectureSeminaristes
from app.utils.finance_utils import create_inscription_entry
from app.utils.rate_limit import rate_limit
from app.utils.uploads import read_image_upload

from app.models.schemas import (
    RegistrationCreate, RegistrationUpdate, RegistrationResponse, RegistrationCreatedResponse,
    ManualPaymentDecision, PaymentStatusResponse, PaymentDetailResponse,
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



# ============================================
# PAIEMENT : VÉRIFICATION OBLIGATOIRE DU REÇU (B9 / B10 / B11)
# ============================================
# Statuts : "en_attente" → "verifie" | "rejete" | "erreur" ; "completed" = inscription antérieure
# à la vérification obligatoire (considérée vérifiée, statut affiché "verifie_historique").
STATUTS_VERIFIES = ("verifie", "completed")
MAX_ESSAIS_MATRICULE = 3


def statut_paiement(registration) -> str:
    return "verifie_historique" if registration.payment_status == "completed" else registration.payment_status


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _reserver_transaction(transaction_id: str, matricule: str) -> bool:
    """Réserve l'ID de transaction (index unique) : une transaction ne valide qu'une inscription."""
    if await prisma.registration.find_first(
        where={"transaction_id": transaction_id, "payment_status": {"in": list(STATUTS_VERIFIES)},
               "NOT": [{"matricule": matricule}]}
    ):
        return False  # inscription antérieure (non encore reportée dans le registre)
    try:
        await prisma.usedtransaction.create(data={"transaction_id": transaction_id, "matricule": matricule})
        return True
    except UniqueViolationError:
        return False


async def _enregistrer_verification(registration, statut: str, methode: str, verified_by: str,
                                    message: str, transaction_id: str = None, score: int = None,
                                    motif: str = None):
    """Met à jour l'inscription et trace la tentative ; crée l'entrée financière si vérifiée."""
    data = {"payment_status": statut, "payment_message": message, "payment_method": methode,
            "payment_verified_by": verified_by}
    if statut == "verifie":
        data.update({"payment_verified_at": datetime.now(timezone.utc), "transaction_id": transaction_id})
    await prisma.registration.update(where={"matricule": registration.matricule}, data=data)
    await prisma.paymentverification.create(data={
        "matricule": registration.matricule, "statut": statut, "methode": methode,
        "transaction_id": transaction_id, "score": score, "motif": motif, "verified_by": verified_by,
    })
    if statut == "verifie":
        # Le paiement n'entre en finances qu'une fois vérifié, au tarif unique du serveur
        await create_inscription_entry(
            matricule=registration.matricule,
            nom=registration.nom,
            prenom=registration.prenom,
            montant=float(settings.REGISTRATION_FEE),
            registration_date=registration.registration_date,
        )
    logger.info("Paiement %s : %s (%s)", registration.matricule, statut, methode)


def _registration_response(result, verification_token: str = None) -> dict:
    return dict(
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
        photo_url=result.photo_url,
        validated=result.validated,
        registration_date=result.registration_date,
        verification_token=verification_token,
    )


# CREATE - Créer une inscription (avec le JSON du front)
@router.post("", response_model=RegistrationCreatedResponse, status_code=201,
             dependencies=[Depends(rate_limit("registrations", 20, 60))])
async def create_registration(data: RegistrationCreate):
    """
    Créer une inscription « en attente » de vérification du paiement.
    Le jeton renvoyé (verification_token, affiché une seule fois) permet ensuite à l'inscrit
    d'envoyer son reçu sur POST /registrations/{matricule}/receipt.
    """
    personal = data.personalInfo
    dormitory = data.dormitoryInfo
    health = data.healthInfo
    payment = data.paymentInfo

    dortoir = await prisma.dortoir.find_unique(where={"code": dormitory.dortoirId})
    if not dortoir:
        raise HTTPException(status_code=404, detail="Dortoir non trouvé")

    if dortoir.gender != personal.sexe:
        raise HTTPException(status_code=400, detail=f"Ce dortoir est réservé au genre {dortoir.gender}")

    # Retour rapide si la transaction déclarée a déjà servi (le contrôle définitif a lieu à la vérification)
    if await prisma.usedtransaction.find_unique(where={"transaction_id": payment.transactionId}) or \
            await prisma.registration.find_first(where={"transaction_id": payment.transactionId}):
        raise HTTPException(
            status_code=409,
            detail="L'ID de transaction fourni a déjà été utilisée pour une autre inscription"
        )

    # B10 : réservation atomique d'une place (deux inscriptions simultanées ne dépassent pas la capacité)
    reserved = await prisma.dortoir.update_many(
        where={"code": dortoir.code, "current_count": {"lt": dortoir.capacity}},
        data={"current_count": {"increment": 1}},
    )
    if not reserved:
        raise HTTPException(status_code=409, detail="Le dortoir sélectionné est complet")

    token = secrets.token_urlsafe(32)
    registration = None
    try:
        # B8 : l'index unique de Registration.matricule est le dernier rempart contre les doublons
        for _ in range(MAX_ESSAIS_MATRICULE):
            matricule = await generate_matricule(dormitory.dortoirId)
            try:
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
                        "date_naissance": (
                            datetime.combine(personal.dateNaissance, datetime.min.time(), tzinfo=timezone.utc)
                            if personal.dateNaissance else None
                        ),
                        "dortoir_code": dormitory.dortoirId,
                        "allergie": health.allergie or "RAS",
                        "antecedent_medical": health.antecedentMedical or "Néant",
                        "payment_status": "en_attente",
                        "payment_message": "Reçu en attente de vérification",
                        "transaction_id": payment.transactionId,
                        "receipt_url": payment.receiptUrl,
                        "receipt_token_hash": _hash_token(token),
                    }
                )
                break
            except UniqueViolationError:
                continue
        if registration is None:
            raise HTTPException(status_code=503, detail="Inscription momentanément impossible, veuillez réessayer")
    finally:
        if registration is None:
            # Libérer la place réservée si l'inscription n'a pas été créée
            await prisma.dortoir.update(where={"code": dortoir.code}, data={"current_count": {"decrement": 1}})

    result = await prisma.registration.find_unique(where={"id": registration.id}, include={"dortoir": True})
    return _registration_response(result, verification_token=token)


@router.post("/{matricule}/receipt", response_model=PaymentStatusResponse,
             dependencies=[Depends(rate_limit("receipt-submit", 10, 60))])
async def submit_receipt(matricule: str, token: str = Form(..., max_length=200), file: UploadFile = File(...)):
    """
    Envoi du reçu par l'inscrit (jeton reçu à l'inscription) : le serveur vérifie le reçu
    et enregistre le résultat. Statut renvoyé : verifie, rejete ou erreur.
    """
    registration = await prisma.registration.find_unique(where={"matricule": matricule})
    # Réponse identique si le matricule est inconnu ou le jeton faux (pas d'énumération)
    if not registration or not registration.receipt_token_hash or \
            not hmac.compare_digest(registration.receipt_token_hash, _hash_token(token)):
        raise HTTPException(status_code=403, detail="Matricule ou jeton de vérification invalide")

    if registration.payment_status in STATUTS_VERIFIES:
        raise HTTPException(status_code=409, detail="Le paiement de cette inscription est déjà vérifié")

    contents = await read_image_upload(file, settings.MAX_UPLOAD_BYTES)
    try:
        text = ocr_processor.process_image(contents)
        analysis = await receipt_analyzer.analyze_receipt(text, settings.REGISTRATION_FEE)
    except Exception:
        logger.exception("Vérification du reçu impossible pour %s", matricule)
        message = "La vérification du reçu a échoué, veuillez réessayer"
        await _enregistrer_verification(registration, "erreur", "ocr", "ocr", message)
        return {"matricule": matricule, "statut": "erreur", "message": message}

    transaction_id = analysis["extractedData"]["transactionId"]
    if analysis["isValid"] and transaction_id and await _reserver_transaction(transaction_id, matricule):
        message = "Paiement vérifié avec succès"
        await _enregistrer_verification(registration, "verifie", "ocr", "ocr", message,
                                        transaction_id=transaction_id, score=analysis["score"])
        return {"matricule": matricule, "statut": "verifie", "message": message}

    message = analysis["message"] if not analysis["isValid"] else \
        "Cette transaction a déjà été utilisée pour une autre inscription"
    await _enregistrer_verification(registration, "rejete", "ocr", "ocr", message,
                                    transaction_id=transaction_id, score=analysis["score"])
    return {"matricule": matricule, "statut": "rejete", "message": message}


@router.get("/{matricule}/paiement", response_model=PaymentDetailResponse, dependencies=[RequireFinances])
async def get_payment(matricule: str):
    """État du paiement et historique des vérifications (admin, finances)"""
    registration = await prisma.registration.find_unique(where={"matricule": matricule})
    if not registration:
        raise HTTPException(status_code=404, detail="Inscription non trouvée")
    history = await prisma.paymentverification.find_many(
        where={"matricule": matricule}, order={"created_at": "desc"}
    )
    return {
        "matricule": matricule,
        "statut": statut_paiement(registration),
        "message": registration.payment_message,
        "methode": registration.payment_method,
        "verified_by": registration.payment_verified_by,
        "verified_at": registration.payment_verified_at,
        "transaction_id": registration.transaction_id,
        "historique": [h.model_dump() for h in history],
    }


@router.patch("/{matricule}/paiement", response_model=PaymentStatusResponse)
async def validate_payment_manually(matricule: str, data: ManualPaymentDecision, current_user=RequireFinances):
    """Validation ou rejet manuel du paiement (admin, finances), motif obligatoire et tracé"""
    registration = await prisma.registration.find_unique(where={"matricule": matricule})
    if not registration:
        raise HTTPException(status_code=404, detail="Inscription non trouvée")
    if registration.payment_status in STATUTS_VERIFIES:
        raise HTTPException(status_code=409, detail="Le paiement de cette inscription est déjà vérifié")

    if data.decision == "verifie":
        if not await _reserver_transaction(data.transaction_id, matricule):
            raise HTTPException(status_code=409, detail="Cette transaction a déjà été utilisée pour une autre inscription")
        message = "Paiement vérifié manuellement"
    else:
        message = "Paiement rejeté après vérification manuelle"

    await _enregistrer_verification(
        registration, data.decision, "manuel", current_user.username, message,
        transaction_id=data.transaction_id if data.decision == "verifie" else None, motif=data.motif,
    )
    return {"matricule": matricule, "statut": data.decision, "message": message}


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