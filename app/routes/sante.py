"""
Module Santé (Partie 6) : fiche médicale, consultations, informations médicales.

Règles (spécification B6-B11, Partie II) :
- accès réservé aux rôles "sante" et "admin" ;
- chaque lecture, création, modification et export est tracé dans MedicalAccessLog,
  sans jamais y copier de contenu médical ;
- aucune suppression : l'historique médical est conservé (pas de règle de suppression définie).
"""
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.database import prisma
from app.models.sante_schemas import (
    ConsultationCreate, ConsultationExport, ConsultationListResponse, ConsultationResponse,
    ConsultationUpdate, FicheMedicale, InformationsMedicalesUpdate, StatistiquesSante,
)
from app.utils.auth import OBJECT_ID_PATTERN, ROLE_SANTE, RequireSante, get_current_user, normalize_role
from app.utils.rate_limit import client_ip

router = APIRouter(prefix="/sante", tags=["Module Santé"], dependencies=[RequireSante])


# ============================================
# HELPERS
# ============================================

async def log_medical_access(request: Request, user, action: str, matricule: str,
                             consultation_id: Optional[str] = None, fields: Optional[list] = None):
    """Tracer un accès aux données médicales (noms de champs uniquement, jamais les valeurs)."""
    await prisma.medicalaccesslog.create(
        data={
            "action": action,
            "matricule": matricule,
            "consultation_id": consultation_id,
            "fields_changed": sorted(fields or []),
            "user_id": user.id,
            "username": user.username,
            "role": user.role,
            "ip_address": client_ip(request),
        }
    )


async def get_patient_or_404(matricule: str):
    patient = await prisma.registration.find_unique(
        where={"matricule": matricule},
        include={"dortoir": True, "seminaristes": True}
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient non trouvé")
    return patient


async def get_consultation_or_404(consultation_id: str):
    # Un identifiant mal formé ne doit pas provoquer d'erreur Prisma (500)
    if not OBJECT_ID_PATTERN.match(consultation_id):
        raise HTTPException(status_code=404, detail="Consultation non trouvée")
    consultation = await prisma.consultation.find_unique(where={"id": consultation_id})
    if not consultation:
        raise HTTPException(status_code=404, detail="Consultation non trouvée")
    return consultation


async def resolve_medecin(medecin_id: Optional[str], current_user) -> str:
    """Le médecin responsable doit être un compte actif de rôle "sante"."""
    if medecin_id is None:
        if current_user.role == ROLE_SANTE:
            return current_user.id
        raise HTTPException(status_code=422, detail="medecin_responsable_id est requis")

    medecin = None
    if OBJECT_ID_PATTERN.match(medecin_id):
        medecin = await prisma.user.find_unique(where={"id": medecin_id})
    if not medecin or not medecin.is_active or normalize_role(medecin.role) != ROLE_SANTE:
        raise HTTPException(status_code=422, detail="Médecin responsable invalide")
    return medecin.id


async def serialize_consultations(consultations) -> list:
    """Ajouter le nom du médecin responsable (une seule requête)."""
    ids = list({c.medecin_responsable_id for c in consultations})
    names = {}
    if ids:
        medecins = await prisma.user.find_many(where={"id": {"in": ids}})
        names = {m.id: f"{m.prenom} {m.nom}" for m in medecins}
    return [
        ConsultationResponse(**c.model_dump(exclude={"seminariste"}), medecin_responsable=names.get(c.medecin_responsable_id))
        for c in consultations
    ]


async def build_fiche(patient) -> FicheMedicale:
    seminariste = patient.seminaristes[0] if patient.seminaristes else None
    nombre = await prisma.consultation.count(where={"matricule": patient.matricule})
    return FicheMedicale(
        patient={
            "matricule": patient.matricule,
            "nom_prenoms": f"{patient.nom} {patient.prenom}",
            "niveau": seminariste.niveau if seminariste else None,
            "sexe": patient.sexe,
            "date_naissance": patient.date_naissance.date() if patient.date_naissance else None,
            "age": patient.age,
            "dortoir": patient.dortoir.name if patient.dortoir else None,
            "dortoir_code": patient.dortoir_code,
            "niveau_scolaire": patient.niveau_academique,
            "photo_url": patient.photo_url,
        },
        informations_medicales={
            "contact_parent": patient.contact_parent,
            "allergies": patient.allergie,
            "antecedents_medicaux": patient.antecedent_medical,
        },
        nombre_consultations=nombre,
    )


# ============================================
# FICHE MÉDICALE DU PATIENT
# ============================================

@router.get("/patients/{matricule}/fiche-medicale", response_model=FicheMedicale)
async def get_fiche_medicale(matricule: str, request: Request, current_user=Depends(get_current_user)):
    """Informations générales et médicales du patient"""
    patient = await get_patient_or_404(matricule)
    fiche = await build_fiche(patient)
    await log_medical_access(request, current_user, "READ_FICHE", matricule)
    return fiche


@router.patch("/patients/{matricule}/informations-medicales", response_model=FicheMedicale)
async def update_informations_medicales(matricule: str, data: InformationsMedicalesUpdate, request: Request,
                                        current_user=Depends(get_current_user)):
    """Mettre à jour contact du parent, allergies, antécédents et date de naissance"""
    await get_patient_or_404(matricule)

    mapping = {"contact_parent": "contact_parent", "allergies": "allergie",
               "antecedents_medicaux": "antecedent_medical", "date_naissance": "date_naissance"}
    update_data = {}
    for field in data.model_fields_set:
        value = getattr(data, field)
        if field == "date_naissance" and value is not None:
            value = datetime.combine(value, time.min, tzinfo=timezone.utc)
        update_data[mapping[field]] = value

    await prisma.registration.update(where={"matricule": matricule}, data=update_data)
    await log_medical_access(request, current_user, "UPDATE_INFOS", matricule, fields=list(data.model_fields_set))
    return await build_fiche(await get_patient_or_404(matricule))


# ============================================
# CONSULTATIONS
# ============================================

@router.get("/patients/{matricule}/consultations", response_model=ConsultationListResponse)
async def list_consultations(
    matricule: str,
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user=Depends(get_current_user)
):
    """Historique des consultations du patient (plus récente en premier)"""
    await get_patient_or_404(matricule)
    where = {"matricule": matricule}
    total = await prisma.consultation.count(where=where)
    consultations = await prisma.consultation.find_many(
        where=where,
        order={"date_consultation": "desc"},
        skip=(page - 1) * limit,
        take=limit
    )
    await log_medical_access(request, current_user, "LIST_CONSULTATIONS", matricule)
    return {"total": total, "page": page, "limit": limit, "data": await serialize_consultations(consultations)}


@router.post("/patients/{matricule}/consultations", response_model=ConsultationResponse, status_code=201)
async def create_consultation(matricule: str, data: ConsultationCreate, request: Request,
                              current_user=Depends(get_current_user)):
    """Créer une consultation (les consultations précédentes ne sont jamais modifiées)"""
    await get_patient_or_404(matricule)
    medecin_id = await resolve_medecin(data.medecin_responsable_id, current_user)

    consultation = await prisma.consultation.create(
        data={
            "matricule": matricule,
            "date_consultation": data.date_consultation,
            "medecin_responsable_id": medecin_id,
            "motif_consultation": data.motif_consultation,
            "observations_diagnostic": data.observations_diagnostic,
            "traitement_prescrit": data.traitement_prescrit,
            "notes_medicales_supplementaires": data.notes_medicales_supplementaires,
            "created_by": current_user.username,
        }
    )
    await log_medical_access(request, current_user, "CREATE", matricule, consultation.id)
    return (await serialize_consultations([consultation]))[0]


@router.get("/consultations/{consultation_id}", response_model=ConsultationResponse)
async def get_consultation(consultation_id: str, request: Request, current_user=Depends(get_current_user)):
    """Détail d'une consultation"""
    consultation = await get_consultation_or_404(consultation_id)
    await log_medical_access(request, current_user, "READ_CONSULTATION", consultation.matricule, consultation.id)
    return (await serialize_consultations([consultation]))[0]


@router.patch("/consultations/{consultation_id}", response_model=ConsultationResponse)
async def update_consultation(consultation_id: str, data: ConsultationUpdate, request: Request,
                              current_user=Depends(get_current_user)):
    """Modifier les informations d'une consultation (le patient rattaché ne change pas)"""
    consultation = await get_consultation_or_404(consultation_id)
    update_data = {field: getattr(data, field) for field in data.model_fields_set}
    if "medecin_responsable_id" in update_data:
        update_data["medecin_responsable_id"] = await resolve_medecin(update_data["medecin_responsable_id"], current_user)

    updated = await prisma.consultation.update(where={"id": consultation.id}, data=update_data)
    await log_medical_access(request, current_user, "UPDATE", consultation.matricule, consultation.id,
                             fields=list(data.model_fields_set))
    return (await serialize_consultations([updated]))[0]


@router.get("/consultations/{consultation_id}/export", response_model=ConsultationExport)
async def export_consultation(consultation_id: str, request: Request, current_user=Depends(get_current_user)):
    """Données de la fiche de consultation pour le PDF (généré côté frontend) ; l'export est tracé"""
    consultation = await get_consultation_or_404(consultation_id)
    patient = await get_patient_or_404(consultation.matricule)
    export = {
        "fiche": await build_fiche(patient),
        "consultation": (await serialize_consultations([consultation]))[0],
    }
    await log_medical_access(request, current_user, "EXPORT", consultation.matricule, consultation.id)
    return export


# ============================================
# STATISTIQUES
# ============================================

# Valeurs par défaut des formulaires signifiant « rien à signaler »
VALEURS_NEANT = ["", "RAS", "Néant", "Neant", "Aucun", "Aucune"]


def _renseigne(field: str) -> dict:
    return {"AND": [{field: {"not": None}}, {field: {"not_in": VALEURS_NEANT}}]}


@router.get("/statistiques", response_model=StatistiquesSante)
async def get_statistiques():
    """Statistiques du module santé, calculées en base (agrégats, aucune donnée nominative)"""
    now = datetime.now(timezone.utc)
    debut_jour = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    debut_periode = debut_jour - timedelta(days=6)

    par_sexe = await prisma.registration.group_by(by=["sexe"], count=True)
    seminaristes_par_sexe = {g["sexe"]: g["_count"]["_all"] for g in par_sexe if g["sexe"] in ("M", "F")}

    par_patient = await prisma.consultation.group_by(by=["matricule"], count=True)
    consultations_par_matricule = {g["matricule"]: g["_count"]["_all"] for g in par_patient}
    consultations_par_sexe = {"M": 0, "F": 0}
    if consultations_par_matricule:
        patients = await prisma.registration.find_many(
            where={"matricule": {"in": list(consultations_par_matricule)}}
        )
        for p in patients:
            if p.sexe in consultations_par_sexe:
                consultations_par_sexe[p.sexe] += consultations_par_matricule[p.matricule]

    recentes = await prisma.consultation.find_many(where={"date_consultation": {"gte": debut_periode}})
    par_jour = {(debut_periode + timedelta(days=i)).date(): 0 for i in range(7)}
    for c in recentes:
        jour = (c.date_consultation if c.date_consultation.tzinfo else c.date_consultation.replace(tzinfo=timezone.utc)).date()
        if jour in par_jour:
            par_jour[jour] += 1

    return {
        "total_seminaristes": sum(g["_count"]["_all"] for g in par_sexe),
        "seminaristes_par_sexe": seminaristes_par_sexe,
        "total_consultations": sum(consultations_par_matricule.values()),
        "consultations_aujourdhui": par_jour.get(now.date(), 0),
        "patients_consultes": len(consultations_par_matricule),
        "consultations_par_sexe": consultations_par_sexe,
        "patients_avec_allergies": await prisma.registration.count(where=_renseigne("allergie")),
        "patients_avec_antecedents": await prisma.registration.count(where=_renseigne("antecedent_medical")),
        "consultations_7_derniers_jours": [{"date": d, "nombre": n} for d, n in par_jour.items()],
    }
