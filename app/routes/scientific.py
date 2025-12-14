from fastapi import APIRouter, HTTPException, Query, Depends
from typing import Optional, List
from datetime import datetime

from app.models.scientific_schemas import (NoteUpdate, NoteResponse,
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
        niveau_academique: Optional[str] = None,
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
    if niveau:
        where["niveau"] = niveau
    if niveau_academique:
        where["niveau_academique"] = niveau_academique

    total = await prisma.registration.count(where=where)

    seminaristes = await prisma.registration.find_many(
        where=where,
        skip=(page - 1) * limit,
        take=limit,
        include={
            "dortoir": True,
            "notes": {
                "where": {"type": "TEST_ENTREE"}
            },
            "seminaristes": True
        }
    )

    # Trier en Python
    seminaristes_sorted = sorted(seminaristes, key=lambda s: (s.nom, s.prenom))

    data = []
    for s in seminaristes_sorted:
        note_entree = s.notes[0].note if s.notes else None
        niveau = (
            s.seminaristes[0].niveau if s.seminaristes else None
        )

        data.append({
            "id": s.id,
            "matricule": s.matricule,
            "nom": s.nom,
            "prenom": s.prenom,
            "sexe": s.sexe,
            "age": s.age,
            "niveau_academique": s.niveau_academique,
            "commune_habitation": s.commune_habitation,
            "contact_parent": s.contact_parent,
            "contact_seminariste": s.contact_seminariste,
            "dortoir": s.dortoir.name if s.dortoir else None,
            "allergie": s.allergie,
            "antecedent_medical": s.antecedent_medical,
            "registration_date": s.registration_date,
            "photo_url": s.photo_url,
            "note_entree": note_entree,
            "niveau": niveau
        })

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "data": data
    }


# ============================================
# NOTES (CRUD)
# ============================================

@router.post("/test-entree", status_code=201)
async def create_test_entree(
    matricule: str,
    note: float,
    niveau: str,
    created_by: str = "admin"
):
    """
    Enregistrer la note du test d'entrée
    + mettre à jour le niveau du séminariste
    """

    if not 0 <= note <= 20:
        raise HTTPException(status_code=400, detail="La note doit être sur 20")

    # Vérifier inscription
    registration = await prisma.registration.find_unique(
        where={"matricule": matricule}
    )
    if not registration:
        raise HTTPException(status_code=404, detail="Séminariste non trouvé")

    # Vérifier ou créer seminariste
    seminariste = await prisma.seminariste.find_unique(
        where={"matricule": matricule}
    )

    if not seminariste:
        seminariste = await prisma.seminariste.create(
            data={
                "matricule": matricule,
                "niveau": niveau
            }
        )
    else:
        # Mettre à jour le niveau
        await prisma.seminariste.update(
            where={"matricule": matricule},
            data={"niveau": niveau}
        )

    # Créer la note du test d'entrée
    note_entree = await prisma.note.create(
        data={
            "matricule": matricule,
            "note": note,
            "type": "TEST_ENTREE",
            "libelle": "Test de niveau à l'entrée",
            "created_by": created_by
        }
    )

    return {
        "message": "Test de niveau enregistré avec succès",
        "matricule": matricule,
        "note": note,
        "niveau": niveau
    }

@router.post("/notes", status_code=201)
async def add_note_seminaire(
    matricule: str,
    note: float,
    libelle: Optional[str] = None,
    created_by: str = "admin"
):
    if not 0 <= note <= 20:
        raise HTTPException(status_code=400, detail="Note invalide")

    seminariste = await prisma.seminariste.find_unique(
        where={"matricule": matricule}
    )
    if not seminariste:
        raise HTTPException(status_code=404, detail="Séminariste non trouvé")

    return await prisma.note.create(
        data={
            "matricule": matricule,
            "note": note,
            "type": "EVALUATION",
            "libelle": libelle,
            "created_by": created_by
        }
    )

@router.get("/notes", response_model=List[NoteResponse])
async def get_notes(
        matricule: Optional[str] = None
):
    """Liste des notes avec filtres"""

    where = {}
    if matricule:
        where["matricule"] = matricule

    notes = await prisma.note.find_many(
        where=where,
        include={"seminariste": True}
    )

    return [
        {
            "id": note.id,
            "matricule": note.matricule,
            "nom_seminariste": note.seminariste.nom,
            "prenom_seminariste": note.seminariste.prenom,
            "note": note.note,
            "type": note.type,
            "libelle":note.libelle,
            "observation": note.observation,
            "created_by": note.created_by,
            "created_at": note.created_at,
            "updated_at": note.updated_at,
        }
        for note in notes
    ]


@router.get("/notes/{id}", response_model=NoteResponse)
async def get_note(id: str):
    """Détail d'une note"""

    note = await prisma.note.find_unique(
        where={"id": id},
        include={"seminariste": True}
    )

    if not note:
        raise HTTPException(status_code=404, detail="Note non trouvée")

    return {
        **note.model_dump(),
        "nom_seminariste": note.seminariste.nom,
        "prenom_seminariste": note.seminariste.prenom
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
        include={"seminariste": True}
    )

    return {
        **note.model_dump(),
        "nom_seminariste": note.seminariste.nom,
        "prenom_seminariste": note.seminariste.prenom
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

    # Récupérer toutes les notes du séminariste
    notes = await prisma.note.find_many(
        where={
            "matricule": data.matricule
        }
    )

    if not notes:
        raise HTTPException(
            status_code=404,
            detail="Aucune note trouvée pour cette période"
        )


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
            "matricule": bulletin.matricule
        }
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

# ============================================
# STATISTIQUES SCIENTIFIQUES
# ============================================

@router.get("/stats/scientifiques", response_model=StatsScientifiques)
async def get_stats_scientifiques():
    """Statistiques du module scientifique"""

    total_seminaristes = await prisma.registration.count()
    total_notes = await prisma.note.count()

    notes = await prisma.note.find_many()

    moyenne_generale = (
        round(sum(n.note for n in notes) / len(notes), 2)
        if notes else 0
    )

    return {
        "total_seminaristes": total_seminaristes,
        "total_notes": total_notes,
        "moyenne_generale": moyenne_generale
    }