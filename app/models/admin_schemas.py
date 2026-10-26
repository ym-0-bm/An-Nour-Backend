from pydantic import BaseModel, EmailStr, StringConstraints, field_validator
from typing import Annotated, Optional, List, Dict
from datetime import datetime

from app.utils.auth import ROLES, normalize_role

PASSWORD_MIN_LENGTH = 8


def _validate_role(value: str) -> str:
    role = normalize_role(value)
    if role not in ROLES:
        raise ValueError(f"Le rôle doit être parmi {list(ROLES)}")
    return role


def _validate_password(value: str) -> str:
    if len(value) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Le mot de passe doit contenir au moins {PASSWORD_MIN_LENGTH} caractères")
    return value


# ============================================
# AUTHENTIFICATION
# ============================================

class UserLogin(BaseModel):
    identifier: Annotated[str, StringConstraints(min_length=1, max_length=254)]  # email OU username
    password: Annotated[str, StringConstraints(min_length=1, max_length=128)]


class UserCreate(BaseModel):
    email: EmailStr
    username: str
    password: str
    nom: str
    prenom: str
    role: str  # "admin", "scientifique", "finances", "sante"

    @field_validator('role')
    @classmethod
    def validate_role(cls, v):
        return _validate_role(v)

    @field_validator('password')
    @classmethod
    def validate_password(cls, v):
        return _validate_password(v)


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    username: Optional[str] = None
    password: Optional[str] = None
    nom: Optional[str] = None
    prenom: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator('role')
    @classmethod
    def validate_role(cls, v):
        return None if v is None else _validate_role(v)

    @field_validator('password')
    @classmethod
    def validate_password(cls, v):
        return None if v is None else _validate_password(v)


class UserResponse(BaseModel):
    id: str
    email: str
    username: str
    nom: str
    prenom: str
    role: str
    is_active: bool
    last_login: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator('role')
    @classmethod
    def canonical_role(cls, v):
        return normalize_role(v)


class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse


class TokenData(BaseModel):
    user_id: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None


# ============================================
# GESTION SÉMINARISTES (Admin)
# ============================================

class SeminaristeCreate(BaseModel):
    nom: str
    prenom: str
    sexe: str
    age: int
    commune_habitation: str
    niveau_academique: str
    dortoir_code: str
    contact_parent: str
    contact_seminariste: Optional[str] = None
    allergie: Optional[str] = "RAS"
    antecedent_medical: Optional[str] = "Néant"

    @field_validator('sexe')
    @classmethod
    def validate_sexe(cls, v):
        if v not in ['M', 'F']:
            raise ValueError('Le sexe doit être M ou F')
        return v

    @field_validator('age')
    @classmethod
    def validate_age(cls, v):
        if not 5 <= v <= 100:
            raise ValueError("L'âge doit être entre 5 et 100 ans")
        return v


class SeminaristeUpdate(BaseModel):
    nom: Optional[str] = None
    prenom: Optional[str] = None
    age: Optional[int] = None
    commune_habitation: Optional[str] = None
    niveau_academique: Optional[str] = None
    dortoir_code: Optional[str] = None
    contact_parent: Optional[str] = None
    contact_seminariste: Optional[str] = None
    allergie: Optional[str] = None
    antecedent_medical: Optional[str] = None
    validated: Optional[bool] = None


class SeminaristeResponse(BaseModel):
    id: str
    matricule: str
    nom: str
    prenom: str
    sexe: str
    age: int
    commune_habitation: str
    niveau_academique: str
    dortoir_code: str
    dortoir_name: Optional[str] = None
    contact_parent: str
    contact_seminariste: Optional[str]
    allergie: Optional[str]
    antecedent_medical: Optional[str]
    payment_status: str
    photo_url: Optional[str]
    validated: bool
    registration_date: datetime

    model_config = {"from_attributes": True}


# ============================================
# DASHBOARD
# ============================================

class DashboardStats(BaseModel):
    inscriptions_total: int
    inscriptions_validees: int
    inscriptions_en_attente: int
    inscriptions_recent_7days: int
    total_transactions: int
    solde_total: float
    total_notes: int
    moyenne_generale_promo: float

    # Démographie
    repartition_sexe: Dict[str, int]
    repartition_niveau: Dict[str, int]
    repartition_dortoir: Dict[str, int]

    # Récents
    inscriptions_recentes: List[dict]
    transactions_recentes: List[dict]


# ============================================
# IMPORT/EXPORT
# ============================================

class ImportResult(BaseModel):
    success: int
    errors: int
    total: int
    error_details: List[dict]


class ExportParams(BaseModel):
    format: str  # "csv", "excel", "pdf"
    filters: Optional[dict] = None
    fields: Optional[List[str]] = None


# ============================================
# GÉNÉRATION DOCUMENTS
# ============================================

class BadgeGenerate(BaseModel):
    matricule: str
    include_qr: bool = True


class DiplomeGenerate(BaseModel):
    matricule: str
    titre: str
    date_obtention: datetime
    mention: Optional[str] = None


class ListePDFGenerate(BaseModel):
    titre: str
    filters: Optional[dict] = None  # niveau, dortoir, sexe
    include_photos: bool = False
    sort_by: str = "nom"  # "nom", "matricule", "dortoir"


# ============================================
# GESTION MEMBRES CO (Comité d'Organisation)
# ============================================

class MembreCOCreate(BaseModel):
    """Schéma pour créer un nouveau membre du CO"""
    nom: str
    prenoms: str
    contact: str
    commission: str  # Ex: "Logistique", "Communication", "Scientifique"
    statut: str  # Ex: "Membre", "Responsable"
    photo_url: Optional[str] = None
    allergies: Optional[str] = "RAS"
    antecedent_medical: Optional[str] = "Néant"


class MembreCOUpdate(BaseModel):
    """Schéma pour mettre à jour un membre du CO"""
    nom: Optional[str] = None
    prenoms: Optional[str] = None
    contact: Optional[str] = None
    commission: Optional[str] = None
    statut: Optional[str] = None
    photo_url: Optional[str] = None
    allergies: Optional[str] = None
    antecedent_medical: Optional[str] = None


class MembreCOResponse(BaseModel):
    """Schéma de réponse pour un membre du CO"""
    id: str
    nom: str
    prenoms: str
    contact: str
    commission: str
    statut: str
    photo_url: Optional[str]
    allergies: Optional[str]
    antecedent_medical: Optional[str]
    created_at: Optional[datetime]

    model_config = {"from_attributes": True}


class MembreCOListResponse(BaseModel):
    """Schéma de réponse pour la liste des membres du CO"""
    total: int
    data: List[MembreCOResponse]