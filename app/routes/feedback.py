from fastapi import APIRouter, HTTPException, Query, Request, Depends
from typing import Optional

from app.models.feedback_schemas import (
    FeedbackCreate, FeedbackResponse, 
    FeedbackListResponse, FeedbackAnalytics
)
from app.database import prisma
from app.utils.auth import get_current_user, RequireAdmin

router = APIRouter(tags=["Feedback"])


# ============================================
# ENDPOINT PUBLIC - SOUMISSION D'AVIS
# ============================================

@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(data: FeedbackCreate, request: Request):
    """
    Soumettre un avis sur le séminaire (endpoint public)
    
    - **sexe**: M ou F (optionnel)
    - **nom**: Nom pour recontact (optionnel)
    - **note_globale**: Note globale 1-5 étoiles
    - **qualite_nourriture**: Note nourriture 1-5 étoiles
    - **confort_dortoirs**: Note dortoirs 1-5 étoiles
    - **qualite_formations**: Note formations 1-5 étoiles
    - **qualite_contenu**: Note contenu 1-5
    - **note_organisation**: Note organisation 0-10
    - **duree_appropriee**: La durée était-elle appropriée ?
    - **recommande**: Recommanderiez-vous ce séminaire ?
    - **points_apprecies**: Ce qui a été apprécié (optionnel)
    - **suggestions**: Suggestions d'amélioration (optionnel)
    """
    # Récupérer l'IP pour traçabilité
    client_ip = request.client.host if request.client else None
    
    feedback = await prisma.feedback.create(
        data={
            "sexe": data.sexe,
            "nom": data.nom,
            "note_globale": data.note_globale,
            "qualite_nourriture": data.qualite_nourriture,
            "confort_dortoirs": data.confort_dortoirs,
            "qualite_formations": data.qualite_formations,
            "qualite_contenu": data.qualite_contenu,
            "note_organisation": data.note_organisation,
            "duree_appropriee": data.duree_appropriee,
            "recommande": data.recommande,
            "points_apprecies": data.points_apprecies,
            "suggestions": data.suggestions,
            "ip_address": client_ip
        }
    )
    
    return FeedbackResponse(
        id=feedback.id,
        sexe=feedback.sexe,
        nom=feedback.nom,
        note_globale=feedback.note_globale,
        qualite_nourriture=feedback.qualite_nourriture,
        confort_dortoirs=feedback.confort_dortoirs,
        qualite_formations=feedback.qualite_formations,
        qualite_contenu=feedback.qualite_contenu,
        note_organisation=feedback.note_organisation,
        duree_appropriee=feedback.duree_appropriee,
        recommande=feedback.recommande,
        points_apprecies=feedback.points_apprecies,
        suggestions=feedback.suggestions,
        created_at=feedback.created_at
    )


# ============================================
# ENDPOINTS ADMIN - CRUD
# ============================================

@router.get("/admin/feedback", response_model=FeedbackListResponse)
async def get_all_feedbacks(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    sexe: Optional[str] = None,
    recommande: Optional[bool] = None
):
    """
    Récupérer tous les avis (admin uniquement)
    
    - **skip**: Nombre d'éléments à ignorer (pagination)
    - **limit**: Nombre maximum d'éléments à retourner
    - **sexe**: Filtrer par sexe (M ou F)
    - **recommande**: Filtrer par recommandation (true/false)
    """
    where = {}
    if sexe:
        where["sexe"] = sexe
    if recommande is not None:
        where["recommande"] = recommande
    
    total = await prisma.feedback.count(where=where)
    feedbacks = await prisma.feedback.find_many(
        where=where,
        skip=skip,
        take=limit,
        order={"created_at": "desc"}
    )
    
    return FeedbackListResponse(
        total=total,
        data=[FeedbackResponse(
            id=f.id,
            sexe=f.sexe,
            nom=f.nom,
            note_globale=f.note_globale,
            qualite_nourriture=f.qualite_nourriture,
            confort_dortoirs=f.confort_dortoirs,
            qualite_formations=f.qualite_formations,
            qualite_contenu=f.qualite_contenu,
            note_organisation=f.note_organisation,
            duree_appropriee=f.duree_appropriee,
            recommande=f.recommande,
            points_apprecies=f.points_apprecies,
            suggestions=f.suggestions,
            created_at=f.created_at
        ) for f in feedbacks]
    )


@router.get("/admin/feedback/analytics", response_model=FeedbackAnalytics)
async def get_feedback_analytics():
    """
    Récupérer les statistiques agrégées des avis (admin uniquement)
    
    Retourne:
    - Nombre total de réponses
    - Moyennes de toutes les notes
    - Répartition par sexe
    - Répartition des notes globales
    - Pourcentage satisfaction durée
    - Pourcentage recommandation
    - Derniers commentaires
    """
    feedbacks = await prisma.feedback.find_many(
        order={"created_at": "desc"}
    )
    total = len(feedbacks)
    
    if total == 0:
        return FeedbackAnalytics(
            total_responses=0,
            moyenne_globale=0,
            moyenne_organisation=0,
            moyenne_nourriture=0,
            moyenne_dortoirs=0,
            moyenne_formations=0,
            moyenne_contenu=0,
            repartition_sexe={"M": 0, "F": 0, "Non spécifié": 0},
            repartition_note_globale={"1": 0, "2": 0, "3": 0, "4": 0, "5": 0},
            pourcentage_duree_ok=0,
            pourcentage_recommande=0,
            derniers_points_apprecies=[],
            dernieres_suggestions=[]
        )
    
    # Calcul des moyennes
    moyenne_globale = sum(f.note_globale for f in feedbacks) / total
    moyenne_organisation = sum(f.note_organisation for f in feedbacks) / total
    moyenne_nourriture = sum(f.qualite_nourriture for f in feedbacks) / total
    moyenne_dortoirs = sum(f.confort_dortoirs for f in feedbacks) / total
    moyenne_formations = sum(f.qualite_formations for f in feedbacks) / total
    moyenne_contenu = sum(f.qualite_contenu for f in feedbacks) / total
    
    # Répartition par sexe
    repartition_sexe = {"M": 0, "F": 0, "Non spécifié": 0}
    for f in feedbacks:
        if f.sexe == "M":
            repartition_sexe["M"] += 1
        elif f.sexe == "F":
            repartition_sexe["F"] += 1
        else:
            repartition_sexe["Non spécifié"] += 1
    
    # Répartition des notes globales
    repartition_note_globale = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
    for f in feedbacks:
        repartition_note_globale[str(f.note_globale)] += 1
    
    # Pourcentages
    duree_ok_count = sum(1 for f in feedbacks if f.duree_appropriee)
    recommande_count = sum(1 for f in feedbacks if f.recommande)
    
    # Derniers commentaires (max 10)
    derniers_points = [f.points_apprecies for f in feedbacks 
                       if f.points_apprecies][:10]
    dernieres_suggestions = [f.suggestions for f in feedbacks 
                             if f.suggestions][:10]
    
    return FeedbackAnalytics(
        total_responses=total,
        moyenne_globale=round(moyenne_globale, 2),
        moyenne_organisation=round(moyenne_organisation, 2),
        moyenne_nourriture=round(moyenne_nourriture, 2),
        moyenne_dortoirs=round(moyenne_dortoirs, 2),
        moyenne_formations=round(moyenne_formations, 2),
        moyenne_contenu=round(moyenne_contenu, 2),
        repartition_sexe=repartition_sexe,
        repartition_note_globale=repartition_note_globale,
        pourcentage_duree_ok=round((duree_ok_count / total) * 100, 1),
        pourcentage_recommande=round((recommande_count / total) * 100, 1),
        derniers_points_apprecies=derniers_points,
        dernieres_suggestions=dernieres_suggestions
    )


@router.get("/admin/feedback/{feedback_id}", response_model=FeedbackResponse)
async def get_feedback(feedback_id: str):
    """
    Récupérer un avis par son ID (admin uniquement)
    """
    feedback = await prisma.feedback.find_unique(where={"id": feedback_id})
    if not feedback:
        raise HTTPException(status_code=404, detail="Avis non trouvé")
    
    return FeedbackResponse(
        id=feedback.id,
        sexe=feedback.sexe,
        nom=feedback.nom,
        note_globale=feedback.note_globale,
        qualite_nourriture=feedback.qualite_nourriture,
        confort_dortoirs=feedback.confort_dortoirs,
        qualite_formations=feedback.qualite_formations,
        qualite_contenu=feedback.qualite_contenu,
        note_organisation=feedback.note_organisation,
        duree_appropriee=feedback.duree_appropriee,
        recommande=feedback.recommande,
        points_apprecies=feedback.points_apprecies,
        suggestions=feedback.suggestions,
        created_at=feedback.created_at
    )


@router.delete("/admin/feedback/{feedback_id}")
async def delete_feedback(feedback_id: str):
    """
    Supprimer un avis (admin uniquement)
    """
    feedback = await prisma.feedback.find_unique(where={"id": feedback_id})
    if not feedback:
        raise HTTPException(status_code=404, detail="Avis non trouvé")
    
    await prisma.feedback.delete(where={"id": feedback_id})
    
    return {"message": "Avis supprimé avec succès", "id": feedback_id}
