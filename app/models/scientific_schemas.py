from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime


# ============================================
# MATIÈRES
# ============================================

class MatiereCreate(BaseModel):
    code: str
    nom: str
    coefficient: float = 1.0
    description: Optional[str] = None

    @field_validator('coefficient')
    @classmethod
    def validate_coefficient(cls, v):
        if v <= 0:
            raise ValueError('Le coefficient doit être positif')
        return v


class MatiereUpdate(BaseModel):
    nom: Optional[str] = None
    coefficient: Optional[float] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class MatiereResponse(BaseModel):
    id: str
    code: str
    nom: str
    coefficient: float
    description: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ============================================
# NOTES
# ============================================

class NoteCreate(BaseModel):
    matricule: str
    matiere_code: str
    note: float
    type_evaluation: str  # "Devoir", "Composition", "Examen"
    observation: Optional[str] = None

    @field_validator('note')
    @classmethod
    def validate_note(cls, v):
        if not 0 <= v <= 20:
            raise ValueError('La note doit être entre 0 et 20')
        return v


class NoteUpdate(BaseModel):
    note: Optional[float] = None
    observation: Optional[str] = None

    @field_validator('note')
    @classmethod
    def validate_note(cls, v):
        if v is not None and not 0 <= v <= 20:
            raise ValueError('La note doit être entre 0 et 20')
        return v


class NoteResponse(BaseModel):
    id: str
    matricule: str
    nom_seminariste: Optional[str] = None
    prenom_seminariste: Optional[str] = None
    matiere_code: str
    nom_matiere: Optional[str] = None
    coefficient: Optional[float] = None
    note: float
    type_evaluation: str
    observation: Optional[str]
    created_by: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ============================================
# BULLETINS
# ============================================

class BulletinGenerate(BaseModel):
    matricule: str
    annee_scolaire: str
    date_conseil: Optional[datetime] = None
    observations: Optional[str] = None


class BulletinResponse(BaseModel):
    id: str
    numero: str
    matricule: str
    nom_seminariste: Optional[str] = None
    prenom_seminariste: Optional[str] = None
    annee_scolaire: str
    moyenne_generale: float
    total_coefficient: float
    rang: Optional[int]
    effectif_classe: Optional[int]
    mention: Optional[str]
    observations: Optional[str]
    date_conseil: Optional[datetime]
    pdf_url: Optional[str]
    generated_at: datetime
    generated_by: str

    model_config = {"from_attributes": True}


class BulletinDetail(BaseModel):
    """Bulletin avec détail des notes par matière"""
    bulletin: BulletinResponse
    notes: List[NoteResponse]
    seminariste: dict


# ============================================
# STATISTIQUES SCIENTIFIQUES
# ============================================

class StatsScientifiques(BaseModel):
    total_seminaristes: int
    total_matieres : int
    total_notes: int
    moyenne_generale: float