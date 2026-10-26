from datetime import date, datetime, timedelta, timezone
from typing import Annotated, List, Optional

from pydantic import BaseModel, StringConstraints, field_validator, model_validator

from app.models.constraints import Contact, LongText

# Texte clinique obligatoire (motif, diagnostic, traitement)
ClinicalText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]

# Tolérance d'horloge pour les dates "pas dans le futur"
CLOCK_SKEW = timedelta(minutes=5)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _check_not_future(value: Optional[datetime]) -> Optional[datetime]:
    if value is not None and _as_utc(value) > datetime.now(timezone.utc) + CLOCK_SKEW:
        raise ValueError("La date de consultation ne peut pas être dans le futur")
    return value


def _reject_nulls(model: BaseModel, fields: tuple) -> None:
    nulls = [f for f in fields if f in model.model_fields_set and getattr(model, f) is None]
    if nulls:
        raise ValueError(f"Champs obligatoires, ne peuvent pas être vides : {', '.join(nulls)}")


# ============================================
# CONSULTATIONS
# ============================================

class ConsultationCreate(BaseModel):
    date_consultation: datetime
    # Facultatif si l'utilisateur connecté a le rôle "sante" (il devient le médecin responsable)
    medecin_responsable_id: Optional[str] = None
    motif_consultation: ClinicalText
    observations_diagnostic: ClinicalText
    traitement_prescrit: ClinicalText
    notes_medicales_supplementaires: Optional[LongText] = None

    _not_future = field_validator('date_consultation')(_check_not_future)


class ConsultationUpdate(BaseModel):
    date_consultation: Optional[datetime] = None
    medecin_responsable_id: Optional[str] = None
    motif_consultation: Optional[ClinicalText] = None
    observations_diagnostic: Optional[ClinicalText] = None
    traitement_prescrit: Optional[ClinicalText] = None
    notes_medicales_supplementaires: Optional[LongText] = None

    _not_future = field_validator('date_consultation')(_check_not_future)

    @model_validator(mode='after')
    def check_fields(self):
        if not self.model_fields_set:
            raise ValueError("Aucun champ à modifier")
        _reject_nulls(self, ('date_consultation', 'medecin_responsable_id', 'motif_consultation',
                             'observations_diagnostic', 'traitement_prescrit'))
        return self


class ConsultationResponse(BaseModel):
    id: str
    matricule: str
    date_consultation: datetime
    medecin_responsable_id: str
    medecin_responsable: Optional[str] = None  # "Prénom Nom" du médecin
    motif_consultation: str
    observations_diagnostic: str
    traitement_prescrit: str
    notes_medicales_supplementaires: Optional[str] = None
    created_by: str
    created_at: datetime
    updated_at: datetime


class ConsultationListResponse(BaseModel):
    total: int
    page: int
    limit: int
    data: List[ConsultationResponse]


# ============================================
# FICHE MÉDICALE
# ============================================

class InformationsMedicalesUpdate(BaseModel):
    contact_parent: Optional[Contact] = None
    allergies: Optional[LongText] = None
    antecedents_medicaux: Optional[LongText] = None
    date_naissance: Optional[date] = None

    @field_validator('date_naissance')
    @classmethod
    def check_date_naissance(cls, v):
        if v is not None and not (date(1900, 1, 1) <= v <= date.today()):
            raise ValueError("Date de naissance invalide")
        return v

    @model_validator(mode='after')
    def check_fields(self):
        if not self.model_fields_set:
            raise ValueError("Aucun champ à modifier")
        _reject_nulls(self, ('contact_parent',))
        return self


class PatientInfo(BaseModel):
    matricule: str
    nom_prenoms: str
    niveau: Optional[str] = None           # Niveau du séminaire (attribué après le test d'entrée)
    sexe: str
    date_naissance: Optional[date] = None  # Absente des inscriptions antérieures à la Partie 6
    age: int
    dortoir: Optional[str] = None
    dortoir_code: str
    niveau_scolaire: str                   # Niveau académique déclaré à l'inscription
    photo_url: Optional[str] = None


class InformationsMedicales(BaseModel):
    contact_parent: str
    allergies: Optional[str] = None
    antecedents_medicaux: Optional[str] = None


class FicheMedicale(BaseModel):
    patient: PatientInfo
    informations_medicales: InformationsMedicales
    nombre_consultations: int


class ConsultationExport(BaseModel):
    """Données nécessaires au PDF de la fiche (généré côté frontend)."""
    fiche: FicheMedicale
    consultation: ConsultationResponse


# ============================================
# STATISTIQUES (agrégats uniquement, aucune donnée nominative)
# ============================================

class RepartitionSexe(BaseModel):
    M: int = 0
    F: int = 0


class ConsultationsJour(BaseModel):
    date: date
    nombre: int


class StatistiquesSante(BaseModel):
    total_seminaristes: int
    seminaristes_par_sexe: RepartitionSexe
    total_consultations: int
    consultations_aujourdhui: int
    patients_consultes: int                 # séminaristes distincts ayant au moins une consultation
    consultations_par_sexe: RepartitionSexe
    patients_avec_allergies: int
    patients_avec_antecedents: int
    consultations_7_derniers_jours: List[ConsultationsJour]
