from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

from app.database import prisma
from app.models.visiteur_schemas import VisiteurCreate, VisiteurResponse, VisiteurListResponse

router = APIRouter(prefix="/visiteurs", tags=["Module Visiteurs"])


# ============================================
# ROUTES
# ============================================

@router.post("", response_model=VisiteurResponse, status_code=201)
async def create_visiteur(data: VisiteurCreate):
    """
    Créer un nouveau visiteur
    
    - **nom**: Nom du visiteur
    - **prenom**: Prénom du visiteur
    - **contact**: Numéro de téléphone du visiteur
    """
    
    visiteur = await prisma.visiteur.create(
        data={
            "nom": data.nom.upper(),
            "prenom": data.prenom.title(),
            "contact": data.contact
        }
    )
    
    return VisiteurResponse(
        id=visiteur.id,
        nom=visiteur.nom,
        prenom=visiteur.prenom,
        contact=visiteur.contact,
        created_at=visiteur.created_at
    )


@router.get("", response_model=VisiteurListResponse)
async def get_visiteurs(
    search: Optional[str] = Query(None, description="Rechercher par nom ou prénom")
):
    """
    Récupérer la liste de tous les visiteurs
    """
    
    where = {}
    if search:
        where["OR"] = [
            {"nom": {"contains": search, "mode": "insensitive"}},
            {"prenom": {"contains": search, "mode": "insensitive"}},
        ]
    
    visiteurs = await prisma.visiteur.find_many(where=where if where else None)
    
    # Trier par date (plus récent en premier)
    visiteurs_sorted = sorted(visiteurs, key=lambda v: v.created_at, reverse=True)
    
    data = [
        VisiteurResponse(
            id=v.id,
            nom=v.nom,
            prenom=v.prenom,
            contact=v.contact,
            created_at=v.created_at
        )
        for v in visiteurs_sorted
    ]
    
    return VisiteurListResponse(
        total=len(data),
        data=data
    )


@router.get("/{visiteur_id}", response_model=VisiteurResponse)
async def get_visiteur(visiteur_id: str):
    """Récupérer un visiteur par son ID"""
    
    visiteur = await prisma.visiteur.find_unique(where={"id": visiteur_id})
    
    if not visiteur:
        raise HTTPException(status_code=404, detail="Visiteur non trouvé")
    
    return VisiteurResponse(
        id=visiteur.id,
        nom=visiteur.nom,
        prenom=visiteur.prenom,
        contact=visiteur.contact,
        created_at=visiteur.created_at
    )


@router.delete("/{visiteur_id}")
async def delete_visiteur(visiteur_id: str):
    """Supprimer un visiteur"""
    
    visiteur = await prisma.visiteur.find_unique(where={"id": visiteur_id})
    
    if not visiteur:
        raise HTTPException(status_code=404, detail="Visiteur non trouvé")
    
    await prisma.visiteur.delete(where={"id": visiteur_id})
    
    return {
        "message": "Visiteur supprimé avec succès",
        "deleted_id": visiteur_id,
        "nom": visiteur.nom,
        "prenom": visiteur.prenom
    }
