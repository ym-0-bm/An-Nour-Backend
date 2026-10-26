from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

from app.database import prisma
from app.utils.auth import RequireAdmin
from app.utils.rate_limit import rate_limit
from app.utils.pagination import Page, set_total
from app.models.visiteur_schemas import VisiteurCreate, VisiteurResponse, VisiteurListResponse

router = APIRouter(prefix="/visiteurs", tags=["Module Visiteurs"])


# ============================================
# ROUTES
# ============================================

@router.post("", response_model=VisiteurResponse, status_code=201,
             dependencies=[Depends(rate_limit("visiteurs", 60, 60))])
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


@router.get("", response_model=VisiteurListResponse, dependencies=[RequireAdmin])
async def get_visiteurs(
    response: Response,
    search: Optional[str] = Query(None, description="Rechercher par nom ou prénom"),
    page: Page = Depends()
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
    
    total = await prisma.visiteur.count(where=where)
    set_total(response, total)
    visiteurs_sorted = await prisma.visiteur.find_many(
        where=where, order={"created_at": "desc"}, skip=page.skip, take=page.limit
    )
    
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
        total=total,
        data=data
    )


@router.get("/{visiteur_id}", response_model=VisiteurResponse, dependencies=[RequireAdmin])
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


@router.delete("/{visiteur_id}", dependencies=[RequireAdmin])
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
