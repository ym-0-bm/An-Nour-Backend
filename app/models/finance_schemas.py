from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime


# ============================================
# TRANSACTIONS
# ============================================

class TransactionCreate(BaseModel):
    type: str  # "ENTREE" ou "SORTIE"
    categorie: str
    montant: float
    libelle: str
    description: Optional[str] = None
    beneficiaire: Optional[str] = None
    payeur: Optional[str] = None
    matricule: Optional[str] = None
    mode_paiement: str
    numero_compte: Optional[str] = None
    piece_justificative: Optional[str] = None
    date_transaction: datetime

    @field_validator('type')
    @classmethod
    def validate_type(cls, v):
        if v not in ['ENTREE', 'SORTIE']:
            raise ValueError('Le type doit être ENTREE ou SORTIE')
        return v

    @field_validator('montant')
    @classmethod
    def validate_montant(cls, v):
        if v <= 0:
            raise ValueError('Le montant doit être positif')
        return v


class TransactionUpdate(BaseModel):
    categorie: Optional[str] = None
    montant: Optional[float] = None
    libelle: Optional[str] = None
    description: Optional[str] = None
    beneficiaire: Optional[str] = None
    payeur: Optional[str] = None
    mode_paiement: Optional[str] = None
    numero_compte: Optional[str] = None
    piece_justificative: Optional[str] = None
    date_transaction: Optional[datetime] = None


class TransactionDelete(BaseModel):
    deleted_reason: str

    @field_validator('deleted_reason')
    @classmethod
    def validate_reason(cls, v):
        if len(v.strip()) < 10:
            raise ValueError('La raison doit contenir au moins 10 caractères')
        return v


class TransactionResponse(BaseModel):
    id: str
    reference: str
    type: str
    categorie: str
    montant: float
    devise: str
    libelle: str
    description: Optional[str]
    beneficiaire: Optional[str]
    payeur: Optional[str]
    matricule: Optional[str]
    nom_seminariste: Optional[str] = None
    mode_paiement: str
    numero_compte: Optional[str]
    piece_justificative: Optional[str]
    statut: str
    date_transaction: datetime
    created_by: str
    created_at: datetime
    updated_at: datetime
    is_deleted: bool
    deleted_at: Optional[datetime]
    deleted_by: Optional[str]
    deleted_reason: Optional[str]

    model_config = {"from_attributes": True}


# ============================================
# AUDIT LOG
# ============================================

class AuditLogResponse(BaseModel):
    id: str
    transaction_id: str
    action: str
    field_changed: Optional[str]
    old_value: Optional[str]
    new_value: Optional[str]
    modified_by: str
    modified_at: datetime
    ip_address: Optional[str]
    user_agent: Optional[str]

    model_config = {"from_attributes": True}


# ============================================
# RAPPORTS FINANCIERS
# ============================================

class RapportGenerate(BaseModel):
    titre: str
    periode_debut: datetime
    periode_fin: datetime
    type_rapport: str  # "mensuel", "trimestriel", "annuel", "personnalise"
    commentaires: Optional[str] = None

    @field_validator('type_rapport')
    @classmethod
    def validate_type(cls, v):
        valid_types = ['mensuel', 'trimestriel', 'annuel', 'personnalise']
        if v not in valid_types:
            raise ValueError(f'Type doit être parmi {valid_types}')
        return v


class RapportResponse(BaseModel):
    id: str
    numero: str
    titre: str
    periode_debut: datetime
    periode_fin: datetime
    type_rapport: str
    total_entrees: float
    total_sorties: float
    solde: float
    nb_transactions: int
    pdf_url: Optional[str]
    generated_by: str
    generated_at: datetime
    commentaires: Optional[str]

    model_config = {"from_attributes": True}


class RapportDetail(BaseModel):
    """Rapport avec détails des transactions"""
    rapport: RapportResponse
    transactions_entrees: List[TransactionResponse]
    transactions_sorties: List[TransactionResponse]
    repartition_categories: dict


# ============================================
# STATISTIQUES FINANCIÈRES
# ============================================

class StatsFinancieres(BaseModel):
    periode_debut: datetime
    periode_fin: datetime
    total_entrees: float
    total_sorties: float
    solde: float
    nb_transactions_entrees: int
    nb_transactions_sorties: int
    moyenne_entree: float
    moyenne_sortie: float
    plus_grosse_entree: Optional[TransactionResponse]
    plus_grosse_sortie: Optional[TransactionResponse]
    repartition_categories_entrees: dict
    repartition_categories_sorties: dict
    evolution_mensuelle: List[dict]


# ============================================
# LISTE PAGINÉE
# ============================================

class PaginatedTransactions(BaseModel):
    total: int
    page: int
    limit: int
    data: List[TransactionResponse]
    total_entrees: float
    total_sorties: float
    solde_periode: float