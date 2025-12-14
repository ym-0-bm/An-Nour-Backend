from fastapi import APIRouter, HTTPException, Query, Request
from typing import Optional, List
from datetime import datetime
import uuid

from app.models.finance_schemas import (
    TransactionCreate, TransactionUpdate, TransactionDelete, TransactionResponse,
    AuditLogResponse, RapportGenerate, RapportResponse, RapportDetail,
    StatsFinancieres, PaginatedTransactions
)
from app.database import prisma

router = APIRouter(prefix="/finances", tags=["Module Finances"])


# ============================================
# HELPER FUNCTIONS
# ============================================

async def create_audit_log(
        transaction_id: str,
        action: str,
        modified_by: str,
        field_changed: Optional[str] = None,
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
):
    """Créer une entrée d'audit log"""
    await prisma.auditlog.create(
        data={
            "transaction_id": transaction_id,
            "action": action,
            "field_changed": field_changed,
            "old_value": old_value,
            "new_value": new_value,
            "modified_by": modified_by,
            "ip_address": ip_address,
            "user_agent": user_agent
        }
    )


def generate_reference(type: str) -> str:
    """Générer référence unique transaction"""
    prefix = "ENT" if type == "ENTREE" else "SOR"
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    unique = str(uuid.uuid4())[:8].upper()
    return f"{prefix}-{timestamp}-{unique}"


# ============================================
# TRANSACTIONS (CRUD avec traçabilité)
# ============================================

@router.post("/transactions", response_model=TransactionResponse, status_code=201)
async def create_transaction(
        data: TransactionCreate,
        created_by: str = "admin",
        request: Request = None
):
    """Créer une transaction"""

    # Si lié à un séminariste, vérifier qu'il existe
    if data.matricule:
        seminariste = await prisma.registration.find_unique(
            where={"matricule": data.matricule}
        )
        if not seminariste:
            raise HTTPException(status_code=404, detail="Séminariste non trouvé")

    # Générer référence unique
    reference = generate_reference(data.type)

    # Créer transaction
    transaction = await prisma.transaction.create(
        data={
            **data.model_dump(),
            "reference": reference,
            "created_by": created_by,
            "statut": "validee"
        }
    )

    # Créer audit log
    ip = request.client.host if request else None
    user_agent = request.headers.get("user-agent") if request else None

    await create_audit_log(
        transaction_id=transaction.id,
        action="CREATE",
        modified_by=created_by,
        ip_address=ip,
        user_agent=user_agent
    )

    # Récupérer avec relations si matricule
    if data.matricule:
        transaction_complete = await prisma.transaction.find_unique(
            where={"id": transaction.id}
        )
        seminariste = await prisma.registration.find_unique(
            where={"matricule": data.matricule}
        )
        return {
            **transaction_complete.model_dump(),
            "nom_seminariste": f"{seminariste.nom} {seminariste.prenom}"
        }

    return transaction


@router.get("/transactions", response_model=PaginatedTransactions)
async def get_transactions(
        page: int = Query(1, ge=1),
        limit: int = Query(20, ge=1, le=100),
        type: Optional[str] = None,
        categorie: Optional[str] = None,
        statut: Optional[str] = None,
        date_debut: Optional[datetime] = None,
        date_fin: Optional[datetime] = None,
        search: Optional[str] = None,
        include_deleted: bool = False
):
    """Liste paginée des transactions avec filtres"""

    where = {}

    # Filtrer transactions supprimées
    if not include_deleted:
        where["is_deleted"] = False

    if type:
        where["type"] = type

    if categorie:
        where["categorie"] = categorie

    if statut:
        where["statut"] = statut

    if date_debut and date_fin:
        where["date_transaction"] = {
            "gte": date_debut,
            "lte": date_fin
        }
    elif date_debut:
        where["date_transaction"] = {"gte": date_debut}
    elif date_fin:
        where["date_transaction"] = {"lte": date_fin}

    if search:
        where["OR"] = [
            {"reference": {"contains": search, "mode": "insensitive"}},
            {"libelle": {"contains": search, "mode": "insensitive"}},
            {"beneficiaire": {"contains": search, "mode": "insensitive"}},
            {"payeur": {"contains": search, "mode": "insensitive"}},
        ]

    # Compter total
    total = await prisma.transaction.count(where=where)

    # Récupérer données
    skip = (page - 1) * limit
    transactions = await prisma.transaction.find_many(
        where=where,
        skip=skip,
        take=limit
    )

    # Trier par date (plus récent en premier)
    transactions_sorted = sorted(
        transactions,
        key=lambda t: t.date_transaction,
        reverse=True
    )

    # Enrichir avec noms séminaristes
    data = []
    for t in transactions_sorted:
        transaction_dict = t.model_dump()
        if t.matricule:
            seminariste = await prisma.registration.find_unique(
                where={"matricule": t.matricule}
            )
            if seminariste:
                transaction_dict["nom_seminariste"] = f"{seminariste.nom} {seminariste.prenom}"
        data.append(transaction_dict)

    # Calculer totaux période
    all_transactions = await prisma.transaction.find_many(where=where)
    total_entrees = sum(t.montant for t in all_transactions if t.type == "ENTREE" and not t.is_deleted)
    total_sorties = sum(t.montant for t in all_transactions if t.type == "SORTIE" and not t.is_deleted)

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "data": data,
        "total_entrees": total_entrees,
        "total_sorties": total_sorties,
        "solde_periode": total_entrees - total_sorties
    }


@router.get("/transactions/{reference}", response_model=TransactionResponse)
async def get_transaction(reference: str):
    """Détail d'une transaction"""

    transaction = await prisma.transaction.find_unique(
        where={"reference": reference}
    )

    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction non trouvée")

    result = transaction.model_dump()

    # Enrichir avec nom séminariste
    if transaction.matricule:
        seminariste = await prisma.registration.find_unique(
            where={"matricule": transaction.matricule}
        )
        if seminariste:
            result["nom_seminariste"] = f"{seminariste.nom} {seminariste.prenom}"

    return result


@router.put("/transactions/{reference}", response_model=TransactionResponse)
async def update_transaction(
        reference: str,
        data: TransactionUpdate,
        modified_by: str = "admin",
        request: Request = None
):
    """Modifier une transaction (avec audit log)"""

    existing = await prisma.transaction.find_unique(
        where={"reference": reference}
    )

    if not existing:
        raise HTTPException(status_code=404, detail="Transaction non trouvée")

    if existing.is_deleted:
        raise HTTPException(
            status_code=400,
            detail="Impossible de modifier une transaction supprimée"
        )

    # Récupérer infos pour audit
    ip = request.client.host if request else None
    user_agent = request.headers.get("user-agent") if request else None

    # Créer audit log pour chaque champ modifié
    update_data = data.model_dump(exclude_unset=True)
    for field, new_value in update_data.items():
        old_value = getattr(existing, field)
        if old_value != new_value:
            await create_audit_log(
                transaction_id=existing.id,
                action="UPDATE",
                modified_by=modified_by,
                field_changed=field,
                old_value=str(old_value),
                new_value=str(new_value),
                ip_address=ip,
                user_agent=user_agent
            )

    # Mettre à jour
    transaction = await prisma.transaction.update(
        where={"reference": reference},
        data=update_data
    )

    result = transaction.model_dump()

    # Enrichir avec nom séminariste
    if transaction.matricule:
        seminariste = await prisma.registration.find_unique(
            where={"matricule": transaction.matricule}
        )
        if seminariste:
            result["nom_seminariste"] = f"{seminariste.nom} {seminariste.prenom}"

    return result


@router.delete("/transactions/{reference}")
async def delete_transaction(
        reference: str,
        data: TransactionDelete,
        deleted_by: str = "admin",
        request: Request = None
):
    """Supprimer (soft delete) une transaction"""

    existing = await prisma.transaction.find_unique(
        where={"reference": reference}
    )

    if not existing:
        raise HTTPException(status_code=404, detail="Transaction non trouvée")

    if existing.is_deleted:
        raise HTTPException(
            status_code=400,
            detail="Transaction déjà supprimée"
        )

    # Soft delete
    transaction = await prisma.transaction.update(
        where={"reference": reference},
        data={
            "is_deleted": True,
            "deleted_at": datetime.now(),
            "deleted_by": deleted_by,
            "deleted_reason": data.deleted_reason
        }
    )

    # Créer audit log
    ip = request.client.host if request else None
    user_agent = request.headers.get("user-agent") if request else None

    await create_audit_log(
        transaction_id=existing.id,
        action="DELETE",
        modified_by=deleted_by,
        old_value=f"Raison: {data.deleted_reason}",
        ip_address=ip,
        user_agent=user_agent
    )

    return {
        "message": "Transaction supprimée avec succès",
        "reference": reference,
        "deleted_at": transaction.deleted_at
    }


@router.post("/transactions/{reference}/restore")
async def restore_transaction(
        reference: str,
        restored_by: str = "admin",
        request: Request = None
):
    """Restaurer une transaction supprimée"""

    existing = await prisma.transaction.find_unique(
        where={"reference": reference}
    )

    if not existing:
        raise HTTPException(status_code=404, detail="Transaction non trouvée")

    if not existing.is_deleted:
        raise HTTPException(
            status_code=400,
            detail="Transaction non supprimée"
        )

    # Restaurer
    transaction = await prisma.transaction.update(
        where={"reference": reference},
        data={
            "is_deleted": False,
            "deleted_at": None,
            "deleted_by": None,
            "deleted_reason": None
        }
    )

    # Créer audit log
    ip = request.client.host if request else None
    user_agent = request.headers.get("user-agent") if request else None

    await create_audit_log(
        transaction_id=existing.id,
        action="RESTORE",
        modified_by=restored_by,
        ip_address=ip,
        user_agent=user_agent
    )

    return {
        "message": "Transaction restaurée avec succès",
        "reference": reference
    }


# ============================================
# TRANSACTIONS SUPPRIMÉES
# ============================================

@router.get("/transactions-deleted")
async def get_deleted_transactions(
        page: int = Query(1, ge=1),
        limit: int = Query(20, ge=1, le=100)
):
    """Liste des transactions supprimées (accessibles)"""

    where = {"is_deleted": True}

    total = await prisma.transaction.count(where=where)

    skip = (page - 1) * limit
    transactions = await prisma.transaction.find_many(
        where=where,
        skip=skip,
        take=limit
    )

    # Trier par date suppression
    transactions_sorted = sorted(
        transactions,
        key=lambda t: t.deleted_at if t.deleted_at else datetime.min,
        reverse=True
    )

    data = []
    for t in transactions_sorted:
        transaction_dict = t.model_dump()
        if t.matricule:
            seminariste = await prisma.registration.find_unique(
                where={"matricule": t.matricule}
            )
            if seminariste:
                transaction_dict["nom_seminariste"] = f"{seminariste.nom} {seminariste.prenom}"
        data.append(transaction_dict)

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "data": data
    }


# ============================================
# AUDIT LOGS
# ============================================

@router.get("/transactions/{reference}/audit", response_model=List[AuditLogResponse])
async def get_transaction_audit_logs(reference: str):
    """Historique des modifications d'une transaction"""

    transaction = await prisma.transaction.find_unique(
        where={"reference": reference}
    )

    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction non trouvée")

    logs = await prisma.auditlog.find_many(
        where={"transaction_id": transaction.id}
    )

    # Trier par date (plus récent en premier)
    logs_sorted = sorted(logs, key=lambda l: l.modified_at, reverse=True)

    return logs_sorted


# ============================================
# RAPPORTS FINANCIERS
# ============================================

@router.post("/rapports/generate", response_model=RapportResponse, status_code=201)
async def generate_rapport(data: RapportGenerate, generated_by: str = "admin"):
    """Générer un rapport financier"""

    # Récupérer transactions de la période
    where = {
        "date_transaction": {
            "gte": data.periode_debut,
            "lte": data.periode_fin
        },
        "is_deleted": False
    }

    transactions = await prisma.transaction.find_many(where=where)

    # Calculer totaux
    total_entrees = sum(t.montant for t in transactions if t.type == "ENTREE")
    total_sorties = sum(t.montant for t in transactions if t.type == "SORTIE")
    solde = total_entrees - total_sorties

    # Générer numéro unique
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    numero = f"RAPP-{timestamp}"

    # Créer rapport
    rapport = await prisma.rapportfinancier.create(
        data={
            "numero": numero,
            "titre": data.titre,
            "periode_debut": data.periode_debut,
            "periode_fin": data.periode_fin,
            "type_rapport": data.type_rapport,
            "total_entrees": total_entrees,
            "total_sorties": total_sorties,
            "solde": solde,
            "nb_transactions": len(transactions),
            "generated_by": generated_by,
            "commentaires": data.commentaires
        }
    )

    return rapport


@router.get("/rapports", response_model=List[RapportResponse])
async def get_rapports(
        type_rapport: Optional[str] = None,
        annee: Optional[int] = None
):
    """Liste des rapports"""

    where = {}

    if type_rapport:
        where["type_rapport"] = type_rapport

    if annee:
        where["periode_debut"] = {
            "gte": datetime(annee, 1, 1),
            "lt": datetime(annee + 1, 1, 1)
        }

    rapports = await prisma.rapportfinancier.find_many(where=where)

    # Trier par date génération
    rapports_sorted = sorted(
        rapports,
        key=lambda r: r.generated_at,
        reverse=True
    )

    return rapports_sorted


@router.get("/rapports/{numero}", response_model=RapportDetail)
async def get_rapport_detail(numero: str):
    """Détail complet d'un rapport avec transactions"""

    rapport = await prisma.rapportfinancier.find_unique(
        where={"numero": numero}
    )

    if not rapport:
        raise HTTPException(status_code=404, detail="Rapport non trouvé")

    # Récupérer transactions de la période
    where = {
        "date_transaction": {
            "gte": rapport.periode_debut,
            "lte": rapport.periode_fin
        },
        "is_deleted": False
    }

    transactions = await prisma.transaction.find_many(where=where)

    # Séparer entrées et sorties
    entrees = [t for t in transactions if t.type == "ENTREE"]
    sorties = [t for t in transactions if t.type == "SORTIE"]

    # Répartition par catégorie
    categories_entrees = {}
    for t in entrees:
        categories_entrees[t.categorie] = categories_entrees.get(t.categorie, 0) + t.montant

    categories_sorties = {}
    for t in sorties:
        categories_sorties[t.categorie] = categories_sorties.get(t.categorie, 0) + t.montant

    # Enrichir transactions avec noms
    async def enrich_transaction(t):
        result = t.model_dump()
        if t.matricule:
            seminariste = await prisma.registration.find_unique(
                where={"matricule": t.matricule}
            )
            if seminariste:
                result["nom_seminariste"] = f"{seminariste.nom} {seminariste.prenom}"
        return result

    entrees_enriched = [await enrich_transaction(t) for t in entrees]
    sorties_enriched = [await enrich_transaction(t) for t in sorties]

    return {
        "rapport": rapport.model_dump(),
        "transactions_entrees": entrees_enriched,
        "transactions_sorties": sorties_enriched,
        "repartition_categories": {
            "entrees": categories_entrees,
            "sorties": categories_sorties
        }
    }


# ============================================
# STATISTIQUES FINANCIÈRES
# ============================================

@router.get("/stats", response_model=StatsFinancieres)
async def get_statistics(
        periode_debut: datetime,
        periode_fin: datetime
):
    """Statistiques financières pour une période"""

    where = {
        "date_transaction": {
            "gte": periode_debut,
            "lte": periode_fin
        },
        "is_deleted": False
    }

    transactions = await prisma.transaction.find_many(where=where)

    entrees = [t for t in transactions if t.type == "ENTREE"]
    sorties = [t for t in transactions if t.type == "SORTIE"]

    total_entrees = sum(t.montant for t in entrees)
    total_sorties = sum(t.montant for t in sorties)

    # Plus grosse transaction
    plus_grosse_entree = max(entrees, key=lambda t: t.montant) if entrees else None
    plus_grosse_sortie = max(sorties, key=lambda t: t.montant) if sorties else None

    # Répartition catégories
    categories_entrees = {}
    for t in entrees:
        categories_entrees[t.categorie] = categories_entrees.get(t.categorie, 0) + t.montant

    categories_sorties = {}
    for t in sorties:
        categories_sorties[t.categorie] = categories_sorties.get(t.categorie, 0) + t.montant

    # Évolution mensuelle
    from collections import defaultdict
    evolution = defaultdict(lambda: {"entrees": 0, "sorties": 0})

    for t in transactions:
        mois = t.date_transaction.strftime("%Y-%m")
        if t.type == "ENTREE":
            evolution[mois]["entrees"] += t.montant
        else:
            evolution[mois]["sorties"] += t.montant

    evolution_list = [
        {"mois": k, **v, "solde": v["entrees"] - v["sorties"]}
        for k, v in sorted(evolution.items())
    ]

    return {
        "periode_debut": periode_debut,
        "periode_fin": periode_fin,
        "total_entrees": total_entrees,
        "total_sorties": total_sorties,
        "solde": total_entrees - total_sorties,
        "nb_transactions_entrees": len(entrees),
        "nb_transactions_sorties": len(sorties),
        "moyenne_entree": total_entrees / len(entrees) if entrees else 0,
        "moyenne_sortie": total_sorties / len(sorties) if sorties else 0,
        "plus_grosse_entree": plus_grosse_entree.model_dump() if plus_grosse_entree else None,
        "plus_grosse_sortie": plus_grosse_sortie.model_dump() if plus_grosse_sortie else None,
        "repartition_categories_entrees": categories_entrees,
        "repartition_categories_sorties": categories_sorties,
        "evolution_mensuelle": evolution_list
    }