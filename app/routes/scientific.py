from fastapi import APIRouter, HTTPException, Query, Depends
from typing import Optional, List
from datetime import datetime

from app.models.scientific_schemas import (
    MatiereCreate, MatiereUpdate, MatiereResponse,
    NoteCreate, NoteUpdate, NoteResponse,
    BulletinGenerate, BulletinResponse, BulletinDetail,
    StatsScientifiques
)
from app.database import prisma

router = APIRouter(prefix="/scientific", tags=["Module Scientifique"])


# ============================================
# LISTE DES SÉMINARISTES (Lecture seule)
# ============================================

@router.get("/seminaristes")
async def get_seminaristes(
        page: int = Query(1, ge=1),
        limit: int = Query(20, ge=1, le=100),
        search: Optional[str] = None,
        dortoir: Optional[str] = None,
        niveau: Optional[str] = None,
        sexe: Optional[str] = None
        
):
    """Liste des séminaristes (lecture seule pour module scientifique)"""

    where = {}
    if search:
        where["OR"] = [
            {"nom": {"contains": search, "mode": "insensitive"}},
            {"prenom": {"contains": search, "mode": "insensitive"}},
            {"matricule": {"contains": search, "mode": "insensitive"}},
        ]
    if sexe:
        where["sexe"] = sexe
    if dortoir:
        where["dortoir_code"] = dortoir

    total = await prisma.registration.count(where=where)

    seminaristes = await prisma.registration.find_many(
        where=where,
        skip=(page - 1) * limit,
        take=limit,
        include={"dortoir": True}
    )

    # Trier en Python
    seminaristes_sorted = sorted(seminaristes, key=lambda s: (s.nom, s.prenom))

    data = [
        {
            "id": s.id,
            "matricule": s.matricule,
            "nom": s.nom,
            "prenom": s.prenom,
            "sexe": s.sexe,
            "age": s.age,
            "niveau_academique": s.niveau_academique,
            "dortoir": s.dortoir.name if s.dortoir else None,
            "photo_url": s.photo_url
        }
        for s in seminaristes_sorted
    ]

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "data": data
    }


# ============================================
# MATIÈRES (CRUD)
# ============================================

@router.post("/matieres", response_model=MatiereResponse, status_code=201)
async def create_matiere(data: MatiereCreate):
    """Créer une matière"""

    # Vérifier unicité du code
    existing = await prisma.matiere.find_unique(where={"code": data.code})
    if existing:
        raise HTTPException(status_code=409, detail="Ce code matière existe déjà")

    matiere = await prisma.matiere.create(data=data.model_dump())
    return matiere


@router.get("/matieres", response_model=List[MatiereResponse])
async def get_matieres(active_only: bool = True):
    """Liste des matières"""

    where = {"is_active": True} if active_only else {}
    matieres = await prisma.matiere.find_many(where=where)
    return matieres


@router.get("/matieres/{code}", response_model=MatiereResponse)
async def get_matiere(code: str):
    """Détail d'une matière"""

    matiere = await prisma.matiere.find_unique(where={"code": code})
    if not matiere:
        raise HTTPException(status_code=404, detail="Matière non trouvée")
    return matiere


@router.put("/matieres/{code}", response_model=MatiereResponse)
async def update_matiere(code: str, data: MatiereUpdate):
    """Modifier une matière"""

    existing = await prisma.matiere.find_unique(where={"code": code})
    if not existing:
        raise HTTPException(status_code=404, detail="Matière non trouvée")

    update_data = data.model_dump(exclude_unset=True)
    matiere = await prisma.matiere.update(
        where={"code": code},
        data=update_data
    )
    return matiere


@router.delete("/matieres/{code}")
async def delete_matiere(code: str):
    """Supprimer (désactiver) une matière"""

    existing = await prisma.matiere.find_unique(where={"code": code})
    if not existing:
        raise HTTPException(status_code=404, detail="Matière non trouvée")

    # Soft delete
    await prisma.matiere.update(
        where={"code": code},
        data={"is_active": False}
    )

    return {"message": "Matière désactivée avec succès", "code": code}


# ============================================
# NOTES (CRUD)
# ============================================

@router.post("/notes", response_model=NoteResponse, status_code=201)
async def create_note(data: NoteCreate, created_by: str = "admin"):
    """Ajouter une note"""

    # Vérifier que le séminariste existe
    seminariste = await prisma.registration.find_unique(
        where={"matricule": data.matricule}
    )
    if not seminariste:
        raise HTTPException(status_code=404, detail="Séminariste non trouvé")

    # Vérifier que la matière existe
    matiere = await prisma.matiere.find_unique(where={"code": data.matiere_code})
    if not matiere:
        raise HTTPException(status_code=404, detail="Matière non trouvée")

    # Créer la note
    note = await prisma.note.create(
        data={
            **data.model_dump(),
            "created_by": created_by
        }
    )

    # Récupérer avec relations
    note_complete = await prisma.note.find_unique(
        where={"id": note.id},
        include={"seminariste": True, "matiere": True}
    )

    return {
        **note_complete.model_dump(),
        "nom_seminariste": note_complete.seminariste.nom,
        "prenom_seminariste": note_complete.seminariste.prenom,
        "nom_matiere": note_complete.matiere.nom,
        "coefficient": note_complete.matiere.coefficient
    }


@router.get("/notes", response_model=List[NoteResponse])
async def get_notes(
        matricule: Optional[str] = None,
        matiere_code: Optional[str] = None,
        annee_scolaire: Optional[str] = None
):
    """Liste des notes avec filtres"""

    where = {}
    if matricule:
        where["matricule"] = matricule
    if matiere_code:
        where["matiere_code"] = matiere_code
    if annee_scolaire:
        where["annee_scolaire"] = annee_scolaire

    notes = await prisma.note.find_many(
        where=where,
        include={"seminariste": True, "matiere": True}
    )

    return [
        {
            **note.model_dump(),
            "nom_seminariste": note.seminariste.nom,
            "prenom_seminariste": note.seminariste.prenom,
            "nom_matiere": note.matiere.nom,
            "coefficient": note.matiere.coefficient
        }
        for note in notes
    ]


@router.get("/notes/{id}", response_model=NoteResponse)
async def get_note(id: str):
    """Détail d'une note"""

    note = await prisma.note.find_unique(
        where={"id": id},
        include={"seminariste": True, "matiere": True}
    )

    if not note:
        raise HTTPException(status_code=404, detail="Note non trouvée")

    return {
        **note.model_dump(),
        "nom_seminariste": note.seminariste.nom,
        "prenom_seminariste": note.seminariste.prenom,
        "nom_matiere": note.matiere.nom,
        "coefficient": note.matiere.coefficient
    }


@router.put("/notes/{id}", response_model=NoteResponse)
async def update_note(id: str, data: NoteUpdate):
    """Modifier une note"""

    existing = await prisma.note.find_unique(where={"id": id})
    if not existing:
        raise HTTPException(status_code=404, detail="Note non trouvée")

    update_data = data.model_dump(exclude_unset=True)
    note = await prisma.note.update(
        where={"id": id},
        data=update_data,
        include={"seminariste": True, "matiere": True}
    )

    return {
        **note.model_dump(),
        "nom_seminariste": note.seminariste.nom,
        "prenom_seminariste": note.seminariste.prenom,
        "nom_matiere": note.matiere.nom,
        "coefficient": note.matiere.coefficient
    }


@router.delete("/notes/{id}")
async def delete_note(id: str):
    """Supprimer une note"""

    existing = await prisma.note.find_unique(where={"id": id})
    if not existing:
        raise HTTPException(status_code=404, detail="Note non trouvée")

    await prisma.note.delete(where={"id": id})
    return {"message": "Note supprimée avec succès", "id": id}


# ============================================
# BULLETINS
# ============================================

@router.post("/bulletins/generate", response_model=BulletinResponse, status_code=201)
async def generate_bulletin(data: BulletinGenerate, generated_by: str = "admin"):
    """Générer un bulletin"""

    # Récupérer toutes les notes du séminariste pour la période
    notes = await prisma.note.find_many(
        where={
            "matricule": data.matricule,
            "annee_scolaire": data.annee_scolaire
        },
        include={"matiere": True}
    )

    if not notes:
        raise HTTPException(
            status_code=404,
            detail="Aucune note trouvée pour cette période"
        )

    # Calculer moyenne générale
    total_points = sum(note.note * note.matiere.coefficient for note in notes)
    total_coef = sum(note.matiere.coefficient for note in notes)
    moyenne = round(total_points / total_coef, 2) if total_coef > 0 else 0

    # Déterminer mention
    if moyenne >= 16:
        mention = "Excellent"
    elif moyenne >= 14:
        mention = "Très bien"
    elif moyenne >= 12:
        mention = "Bien"
    elif moyenne >= 10:
        mention = "Passable"
    else:
        mention = "Insuffisant"

    # Générer numéro unique
    numero = f"BUL-{data.matricule}-{data.annee_scolaire}".replace(" ", "")

    # Créer bulletin
    bulletin = await prisma.bulletin.create(
        data={
            "numero": numero,
            "matricule": data.matricule,
            "annee_scolaire": data.annee_scolaire,
            "moyenne_generale": moyenne,
            "total_coefficient": total_coef,
            "mention": mention,
            "observations": data.observations,
            "date_conseil": data.date_conseil,
            "generated_by": generated_by
        },
        include={"seminariste": True}
    )

    return {
        **bulletin.model_dump(),
        "nom_seminariste": bulletin.seminariste.nom,
        "prenom_seminariste": bulletin.seminariste.prenom
    }


@router.get("/bulletins/{numero}", response_model=BulletinDetail)
async def get_bulletin(numero: str):
    """Détail complet d'un bulletin avec notes"""

    bulletin = await prisma.bulletin.find_unique(
        where={"numero": numero},
        include={"seminariste": True}
    )

    if not bulletin:
        raise HTTPException(status_code=404, detail="Bulletin non trouvé")

    # Récupérer les notes
    notes = await prisma.note.find_many(
        where={
            "matricule": bulletin.matricule,
            "annee_scolaire": bulletin.annee_scolaire
        },
        include={"matiere": True}
    )

    return {
        "bulletin": {
            **bulletin.model_dump(),
            "nom_seminariste": bulletin.seminariste.nom,
            "prenom_seminariste": bulletin.seminariste.prenom
        },
        "notes": [
            {
                **note.model_dump(),
                "nom_matiere": note.matiere.nom,
                "coefficient": note.matiere.coefficient
            }
            for note in notes
        ],
        "seminariste": {
            "matricule": bulletin.seminariste.matricule,
            "nom": bulletin.seminariste.nom,
            "prenom": bulletin.seminariste.prenom,
            "niveau_academique": bulletin.seminariste.niveau_academique,
            "dortoir_code": bulletin.seminariste.dortoir_code
        }
    }


@router.get("/bulletins")
async def get_bulletins(
        matricule: Optional[str] = None,
        annee_scolaire: Optional[str] = None
):
    """Liste des bulletins"""

    where = {}
    if matricule:
        where["matricule"] = matricule
    if annee_scolaire:
        where["annee_scolaire"] = annee_scolaire

    bulletins = await prisma.bulletin.find_many(
        where=where,
        include={"seminariste": True}
    )

    return [
        {
            **b.model_dump(),
            "nom_seminariste": b.seminariste.nom,
            "prenom_seminariste": b.seminariste.prenom
        }
        for b in bulletins
    ]