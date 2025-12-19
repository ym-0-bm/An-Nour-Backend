from fastapi import APIRouter, HTTPException, Query, Request
from typing import Optional, List
from datetime import datetime
import uuid

from app.models.finance_schemas import (
    TransactionCreate, TransactionUpdate, TransactionDelete, TransactionResponse,
    AuditLogResponse, RapportGenerate, RapportResponse, RapportDetail,
    PaginatedTransactions,
    EntreeDonCreate, EntreeVenteCreate, DashboardFinance, PaginatedEntrees,
    SortieCreate, SortieUpdate
)
from app.database import prisma
from app.utils.finance_utils import generate_reference, create_inscription_entry

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


# ============================================
# ENTRÉES - DONS
# ============================================

@router.post("/entrees/don", response_model=TransactionResponse, status_code=201)
async def create_entree_don(
    data: EntreeDonCreate,
    created_by: str = "admin",
    request: Request = None
):
    """
    Ajouter une entrée de type Don
    
    - **montant**: Montant du don en FCFA
    - **donateur**: Nom du donateur
    - **description**: Description optionnelle
    - **mode_paiement**: Mode de paiement (Wave, Espèces, etc.)
    """
    
    reference = generate_reference("ENTREE")
    date = data.date_transaction or datetime.now()
    
    transaction = await prisma.transaction.create(
        data={
            "reference": reference,
            "type": "ENTREE",
            "categorie": "Don",
            "montant": data.montant,
            "libelle": f"Don de {data.donateur}",
            "description": data.description,
            "payeur": data.donateur,
            "mode_paiement": data.mode_paiement,
            "date_transaction": date,
            "created_by": created_by,
            "statut": "validee"
        }
    )
    
    # Audit log
    ip = request.client.host if request else None
    user_agent = request.headers.get("user-agent") if request else None
    
    await create_audit_log(
        transaction_id=transaction.id,
        action="CREATE",
        modified_by=created_by,
        ip_address=ip,
        user_agent=user_agent
    )
    
    return transaction


# ============================================
# ENTRÉES - VENTES
# ============================================

@router.post("/entrees/vente", response_model=TransactionResponse, status_code=201)
async def create_entree_vente(
    data: EntreeVenteCreate,
    created_by: str = "admin",
    request: Request = None
):
    """
    Ajouter une entrée de type Vente
    
    - **montant**: Montant de la vente en FCFA
    - **libelle**: Description de la vente
    - **description**: Détails supplémentaires optionnels
    - **mode_paiement**: Mode de paiement (Espèces, Wave, etc.)
    """
    
    reference = generate_reference("ENTREE")
    date = data.date_transaction or datetime.now()
    
    transaction = await prisma.transaction.create(
        data={
            "reference": reference,
            "type": "ENTREE",
            "categorie": "Vente",
            "montant": data.montant,
            "libelle": data.libelle,
            "description": data.description,
            "mode_paiement": data.mode_paiement,
            "date_transaction": date,
            "created_by": created_by,
            "statut": "validee"
        }
    )
    
    # Audit log
    ip = request.client.host if request else None
    user_agent = request.headers.get("user-agent") if request else None
    
    await create_audit_log(
        transaction_id=transaction.id,
        action="CREATE",
        modified_by=created_by,
        ip_address=ip,
        user_agent=user_agent
    )
    
    return transaction


# ============================================
# ENTRÉES - LISTE
# ============================================

@router.get("/entrees", response_model=PaginatedEntrees)
async def get_entrees(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    categorie: Optional[str] = Query(None, description="Filtrer par catégorie: Inscription, Don, Vente")
):
    """
    Lister toutes les entrées (recettes)
    
    - **categorie**: Filtrer par Inscription, Don, Vente, ou autre
    """
    
    where = {
        "type": "ENTREE",
        "is_deleted": False
    }
    
    if categorie:
        where["categorie"] = categorie
    
    total = await prisma.transaction.count(where=where)
    
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
    
    # Calculer total montant
    all_entrees = await prisma.transaction.find_many(where=where)
    total_montant = sum(t.montant for t in all_entrees)
    
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "data": data,
        "total_montant": total_montant
    }


@router.get("/entrees/{reference}", response_model=TransactionResponse)
async def get_entree(reference: str):
    """Détail d'une entrée"""

    transaction = await prisma.transaction.find_unique(
        where={"reference": reference}
    )

    if not transaction or transaction.type != "ENTREE":
        raise HTTPException(status_code=404, detail="Entrée non trouvée")

    result = transaction.model_dump()

    # Enrichir avec nom séminariste
    if transaction.matricule:
        seminariste = await prisma.registration.find_unique(
            where={"matricule": transaction.matricule}
        )
        if seminariste:
            result["nom_seminariste"] = f"{seminariste.nom} {seminariste.prenom}"

    return result


# ============================================
# SORTIES - CRÉATION
# ============================================

@router.post("/sorties", response_model=TransactionResponse, status_code=201)
async def create_sortie(
        data: SortieCreate,
        created_by: str = "admin",
        request: Request = None
):
    """
    Créer une sortie (dépense)
    
    - **categorie**: Catégorie de dépense (Achat matériel, Transport, Nourriture, Salaires, etc.)
    - **montant**: Montant de la dépense en FCFA
    - **libelle**: Description courte
    - **beneficiaire**: Bénéficiaire du paiement (optionnel)
    - **mode_paiement**: Mode de paiement (défaut: Espèces)
    - **date_transaction**: Date de la transaction (défaut: maintenant)
    """

    # Générer référence unique
    reference = generate_reference("SORTIE")
    date = data.date_transaction or datetime.now()

    # Créer transaction
    transaction = await prisma.transaction.create(
        data={
            "reference": reference,
            "type": "SORTIE",
            "categorie": data.categorie,
            "montant": data.montant,
            "libelle": data.libelle,
            "beneficiaire": data.beneficiaire,
            "mode_paiement": data.mode_paiement,
            "date_transaction": date,
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

    return transaction


# ============================================
# SORTIES - LISTE
# ============================================

@router.get("/sorties", response_model=PaginatedTransactions)
async def get_sorties(
        page: int = Query(1, ge=1),
        limit: int = Query(20, ge=1, le=100),
        categorie: Optional[str] = None,
        date_debut: Optional[datetime] = None,
        date_fin: Optional[datetime] = None,
        search: Optional[str] = None
):
    """Liste paginée des sorties (dépenses)"""

    where = {
        "type": "SORTIE",
        "is_deleted": False
    }

    if categorie:
        where["categorie"] = categorie

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

    # Calculer totaux
    all_sorties = await prisma.transaction.find_many(where=where)
    total_sorties = sum(t.montant for t in all_sorties)

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "data": data,
        "total_entrees": 0,
        "total_sorties": total_sorties,
        "solde_periode": -total_sorties
    }


@router.get("/sorties/{reference}", response_model=TransactionResponse)
async def get_sortie(reference: str):
    """Détail d'une sortie"""

    transaction = await prisma.transaction.find_unique(
        where={"reference": reference}
    )

    if not transaction or transaction.type != "SORTIE":
        raise HTTPException(status_code=404, detail="Sortie non trouvée")

    result = transaction.model_dump()

    # Enrichir avec nom séminariste
    if transaction.matricule:
        seminariste = await prisma.registration.find_unique(
            where={"matricule": transaction.matricule}
        )
        if seminariste:
            result["nom_seminariste"] = f"{seminariste.nom} {seminariste.prenom}"

    return result


@router.put("/sorties/{reference}", response_model=TransactionResponse)
async def update_sortie(
        reference: str,
        data: SortieUpdate,
        modified_by: str = "admin",
        request: Request = None
):
    """Modifier une sortie"""

    existing = await prisma.transaction.find_unique(
        where={"reference": reference}
    )

    if not existing or existing.type != "SORTIE":
        raise HTTPException(status_code=404, detail="Sortie non trouvée")

    if existing.is_deleted:
        raise HTTPException(
            status_code=400,
            detail="Impossible de modifier une sortie supprimée"
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


@router.delete("/sorties/{reference}")
async def delete_sortie(
        reference: str,
        data: TransactionDelete,
        deleted_by: str = "admin",
        request: Request = None
):
    """Supprimer (soft delete) une sortie"""

    existing = await prisma.transaction.find_unique(
        where={"reference": reference}
    )

    if not existing or existing.type != "SORTIE":
        raise HTTPException(status_code=404, detail="Sortie non trouvée")

    if existing.is_deleted:
        raise HTTPException(
            status_code=400,
            detail="Sortie déjà supprimée"
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
        "message": "Sortie supprimée avec succès",
        "reference": reference,
        "deleted_at": transaction.deleted_at
    }


# ============================================
# SORTIES SUPPRIMÉES
# ============================================

@router.get("/sorties-deleted")
async def get_deleted_sorties(
        page: int = Query(1, ge=1),
        limit: int = Query(20, ge=1, le=100)
):
    """Liste des sorties supprimées"""

    where = {"is_deleted": True, "type": "SORTIE"}

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


@router.post("/sorties/{reference}/restore")
async def restore_sortie(
        reference: str,
        restored_by: str = "admin",
        request: Request = None
):
    """Restaurer une sortie supprimée"""

    existing = await prisma.transaction.find_unique(
        where={"reference": reference}
    )

    if not existing or existing.type != "SORTIE":
        raise HTTPException(status_code=404, detail="Sortie non trouvée")

    if not existing.is_deleted:
        raise HTTPException(
            status_code=400,
            detail="Sortie non supprimée"
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
        "message": "Sortie restaurée avec succès",
        "reference": reference
    }


# ============================================
# AUDIT LOGS
# ============================================

@router.get("/audit/{reference}", response_model=List[AuditLogResponse])
async def get_audit_logs(reference: str):
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
# DASHBOARD
# ============================================

@router.get("/dashboard", response_model=DashboardFinance)
async def get_dashboard():
    """
    Récupérer les données pour le tableau de bord financier
    
    Retourne:
    - Totaux par catégorie d'entrée (inscriptions, dons, ventes)
    - Total des sorties
    - Solde global
    - Transactions récentes
    - Répartition pour graphiques
    """
    
    # Récupérer toutes les transactions actives
    all_transactions = await prisma.transaction.find_many(
        where={"is_deleted": False}
    )
    
    entrees = [t for t in all_transactions if t.type == "ENTREE"]
    sorties = [t for t in all_transactions if t.type == "SORTIE"]
    
    # Calculer par catégorie d'entrée
    inscriptions = [t for t in entrees if t.categorie == "Inscription"]
    dons = [t for t in entrees if t.categorie == "Don"]
    ventes = [t for t in entrees if t.categorie == "Vente"]
    autres = [t for t in entrees if t.categorie not in ["Inscription", "Don", "Vente"]]
    
    inscriptions_data = {
        "count": len(inscriptions),
        "montant": sum(t.montant for t in inscriptions)
    }
    dons_data = {
        "count": len(dons),
        "montant": sum(t.montant for t in dons)
    }
    ventes_data = {
        "count": len(ventes),
        "montant": sum(t.montant for t in ventes)
    }
    autres_data = {
        "count": len(autres),
        "montant": sum(t.montant for t in autres)
    }
    
    sorties_data = {
        "count": len(sorties),
        "montant": sum(t.montant for t in sorties)
    }
    
    total_entrees = sum(t.montant for t in entrees)
    total_sorties = sum(t.montant for t in sorties)
    solde = total_entrees - total_sorties
    
    # Transactions récentes (10 dernières)
    transactions_sorted = sorted(
        all_transactions,
        key=lambda t: t.date_transaction,
        reverse=True
    )[:10]
    
    transactions_recentes = []
    for t in transactions_sorted:
        trans_dict = {
            "reference": t.reference,
            "type": t.type,
            "categorie": t.categorie,
            "montant": t.montant,
            "libelle": t.libelle,
            "date_transaction": t.date_transaction.isoformat(),
            "mode_paiement": t.mode_paiement
        }
        if t.matricule:
            seminariste = await prisma.registration.find_unique(
                where={"matricule": t.matricule}
            )
            if seminariste:
                trans_dict["nom_seminariste"] = f"{seminariste.nom} {seminariste.prenom}"
        transactions_recentes.append(trans_dict)
    
    # Répartition pour graphiques
    repartition_entrees = {}
    for t in entrees:
        repartition_entrees[t.categorie] = repartition_entrees.get(t.categorie, 0) + t.montant
    
    repartition_sorties = {}
    for t in sorties:
        repartition_sorties[t.categorie] = repartition_sorties.get(t.categorie, 0) + t.montant
    
    return {
        "inscriptions": inscriptions_data,
        "dons": dons_data,
        "ventes": ventes_data,
        "autres_entrees": autres_data,
        "sorties": sorties_data,
        "total_entrees": total_entrees,
        "total_sorties": total_sorties,
        "solde": solde,
        "transactions_recentes": transactions_recentes,
        "repartition_entrees": repartition_entrees,
        "repartition_sorties": repartition_sorties
    }


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
# SYNCHRONISATION INSCRIPTIONS → FINANCES
# ============================================

@router.post("/sync/inscriptions")
async def sync_all_inscriptions(
    montant_inscription: float = Query(6000, description="Montant par inscription en FCFA"),
    created_by: str = "admin"
):
    """
    Synchroniser TOUTES les inscriptions existantes vers le module finance.
    
    Crée une entrée de type "Inscription" pour chaque registration 
    qui n'a pas encore d'entrée finance associée.
    """
    
    # Récupérer toutes les inscriptions
    registrations = await prisma.registration.find_many()
    
    # Récupérer les matricules déjà synchronisés
    existing_transactions = await prisma.transaction.find_many(
        where={
            "categorie": "Inscription",
            "matricule": {"not": None}
        }
    )
    existing_matricules = {t.matricule for t in existing_transactions}
    
    created_count = 0
    skipped_count = 0
    errors = []
    
    for reg in registrations:
        if reg.matricule in existing_matricules:
            skipped_count += 1
            continue
        
        result = await create_inscription_entry(
            matricule=reg.matricule,
            nom=reg.nom,
            prenom=reg.prenom,
            montant=montant_inscription,
            registration_date=reg.registration_date
        )
        
        if result["success"]:
            created_count += 1
        else:
            errors.append(result)
    
    return {
        "message": "Synchronisation terminée",
        "total_registrations": len(registrations),
        "created": created_count,
        "skipped": skipped_count,
        "errors": len(errors),
        "error_details": errors[:10]
    }


@router.post("/sync/inscription/{matricule}")
async def sync_single_inscription(
    matricule: str,
    montant_inscription: float = Query(6000, description="Montant inscription en FCFA")
):
    """
    Synchroniser UNE inscription spécifique vers le module finance.
    """
    
    registration = await prisma.registration.find_unique(
        where={"matricule": matricule}
    )
    
    if not registration:
        raise HTTPException(status_code=404, detail="Inscription non trouvée")
    
    result = await create_inscription_entry(
        matricule=registration.matricule,
        nom=registration.nom,
        prenom=registration.prenom,
        montant=montant_inscription,
        registration_date=registration.registration_date
    )
    
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "Erreur inconnue"))
    
    return {
        "message": "Synchronisée" if not result["already_exists"] else "Déjà synchronisée",
        **result
    }


@router.get("/sync/status")
async def get_sync_status():
    """
    Vérifier l'état de synchronisation entre inscriptions et finances.
    """
    
    total_registrations = await prisma.registration.count()
    
    synced_transactions = await prisma.transaction.find_many(
        where={
            "categorie": "Inscription",
            "matricule": {"not": None},
            "is_deleted": False
        }
    )
    synced_count = len(synced_transactions)
    
    total_amount = sum(t.montant for t in synced_transactions)
    
    return {
        "total_registrations": total_registrations,
        "synced_count": synced_count,
        "not_synced_count": total_registrations - synced_count,
        "sync_percentage": round((synced_count / total_registrations * 100) if total_registrations > 0 else 0, 2),
        "total_amount": total_amount
    }