from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime

# ============================================
# NOTES
# ============================================

class NoteCreate(BaseModel):
    matricule: str
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
    note: float
    type: str
    libelle: str
    observation: Optional[str]
    created_by: str
    created_at: datetime
    updated_at: Optional[datetime]

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
    total_notes: int
    moyenne_generale: float