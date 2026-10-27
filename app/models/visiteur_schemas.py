from pydantic import BaseModel
from typing import List
from datetime import datetime

from app.models.constraints import Contact, ShortText

# ============================================
# SCHEMAS
# ============================================

class VisiteurCreate(BaseModel):
    """Schéma pour créer un visiteur"""
    nom: ShortText
    prenom: ShortText
    contact: Contact


class VisiteurResponse(BaseModel):
    """Schéma de réponse pour un visiteur"""
    id: str
    nom: str
    prenom: str
    contact: str
    created_at: datetime

    model_config = {"from_attributes": True}


class VisiteurListResponse(BaseModel):
    """Schéma de réponse pour la liste des visiteurs"""
    total: int
    data: List[VisiteurResponse]