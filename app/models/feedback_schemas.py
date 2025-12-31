from pydantic import BaseModel, field_validator
from typing import Optional, Dict, List
from datetime import datetime


# ============================================
# CRÉATION D'UN AVIS
# ============================================

class FeedbackCreate(BaseModel):
    """Schéma pour soumettre un avis"""
    sexe: Optional[str] = None
    nom: Optional[str] = None
    
    # Notes étoiles (1-5)
    note_globale: int
    qualite_nourriture: int
    confort_dortoirs: int
    qualite_formations: int
    qualite_contenu: int
    
    # Note organisation (0-10)
    note_organisation: int
    
    # Questions binaires
    duree_appropriee: bool
    recommande: bool
    
    # Commentaires
    points_apprecies: Optional[str] = None
    suggestions: Optional[str] = None
    
    @field_validator('sexe')
    @classmethod
    def validate_sexe(cls, v):
        if v is not None and v not in ['M', 'F']:
            raise ValueError('Le sexe doit être M ou F')
        return v
    
    @field_validator('note_globale', 'qualite_nourriture', 'confort_dortoirs', 
                     'qualite_formations', 'qualite_contenu')
    @classmethod
    def validate_star_ratings(cls, v):
        if not 1 <= v <= 5:
            raise ValueError('La note doit être entre 1 et 5')
        return v
    
    @field_validator('note_organisation')
    @classmethod
    def validate_organisation(cls, v):
        if not 0 <= v <= 10:
            raise ValueError('La note organisation doit être entre 0 et 10')
        return v


# ============================================
# RÉPONSE API
# ============================================

class FeedbackResponse(BaseModel):
    """Schéma de réponse pour un avis"""
    id: str
    sexe: Optional[str]
    nom: Optional[str]
    
    note_globale: int
    qualite_nourriture: int
    confort_dortoirs: int
    qualite_formations: int
    qualite_contenu: int
    note_organisation: int
    
    duree_appropriee: bool
    recommande: bool
    
    points_apprecies: Optional[str]
    suggestions: Optional[str]
    
    created_at: datetime
    
    model_config = {"from_attributes": True}


class FeedbackListResponse(BaseModel):
    """Schéma pour la liste paginée des avis"""
    total: int
    data: List[FeedbackResponse]


# ============================================
# ANALYTICS
# ============================================

class FeedbackAnalytics(BaseModel):
    """Statistiques agrégées des avis"""
    total_responses: int
    
    # Moyennes des notes
    moyenne_globale: float
    moyenne_organisation: float
    moyenne_nourriture: float
    moyenne_dortoirs: float
    moyenne_formations: float
    moyenne_contenu: float
    
    # Répartitions
    repartition_sexe: Dict[str, int]
    repartition_note_globale: Dict[str, int]  # {"1": 5, "2": 10, ...}
    
    # Pourcentages
    pourcentage_duree_ok: float
    pourcentage_recommande: float
    
    # Top commentaires (les plus récents)
    derniers_points_apprecies: List[str]
    dernieres_suggestions: List[str]
