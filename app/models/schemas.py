from pydantic import BaseModel, StringConstraints, field_validator, model_validator
from typing import Annotated, Literal, Optional, List
from datetime import date, datetime

from app.models.constraints import (
    ShortText, OptionalShortText, Contact, OptionalContact, LongText, Identifier, Url
)


class PersonalInfo(BaseModel):
    nom: ShortText
    prenom: ShortText
    sexe: ShortText
    age: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=3)]
    communeHabitation: ShortText
    niveauAcademique: ShortText
    communeAutre: Optional[OptionalShortText] = ""
    contactParent: Contact
    contactSeminariste: Optional[OptionalContact] = ""
    dateNaissance: Optional[date] = None  # Facultatif (Partie 6)


class DormitoryInfo(BaseModel):
    dortoir: ShortText
    dortoirId: Identifier


class HealthInfo(BaseModel):
    allergie: Optional[LongText] = "RAS"
    antecedentMedical: Optional[LongText] = "Néant"


class PaymentInfo(BaseModel):
    transactionId: Identifier
    receiptUrl: Optional[Url] = None
    amount: int  # Ignoré : le tarif unique est fixé côté serveur (REGISTRATION_FEE)


class RegistrationCreate(BaseModel):
    personalInfo: PersonalInfo
    dormitoryInfo: DormitoryInfo
    healthInfo: HealthInfo
    paymentInfo: PaymentInfo

    @field_validator('personalInfo')
    @classmethod
    def validate_personal_info(cls, v):
        if v.sexe not in ['M', 'F']:
            raise ValueError('Nous n\'acceptons pas les séminaristes de sexe autre que M ou F')

        age = int(v.age)
        if age < 5:
            raise ValueError('Nous n\'acceptons pas les séminaristes de moins de 5 ans')
        elif age > 100:
            raise ValueError('Nous n\'acceptons pas les séminaristes de plus de 100 ans')

        if v.dateNaissance is not None and not (date(1900, 1, 1) <= v.dateNaissance <= date.today()):
            raise ValueError('Date de naissance invalide')

        return v


class RegistrationUpdate(BaseModel):
    allergie: Optional[str] = None
    antecedent_medical: Optional[str] = None
    payment_status: Optional[str] = None
    transaction_id: Optional[str] = None


class DortoirResponse(BaseModel):
    code: str
    name: str
    capacity: int
    current_count: int
    available: int
    gender: str


class RegistrationResponse(BaseModel):
    id: str
    matricule: str
    nom: str
    prenom: str
    sexe: str
    age: int
    niveau_academique: str
    commune_habitation: str
    contact_parent: str
    contact_seminariste: Optional[str] = None
    dortoir_code: str
    dortoir_name: Optional[str] = None
    allergie: Optional[str]
    antecedent_medical: Optional[str]
    payment_status: str
    transaction_id: Optional[str]
    photo_url: Optional[str]
    validated: bool
    registration_date: datetime

    model_config = {
        "from_attributes": True
    }


class PaginatedResponse(BaseModel):
    total: int
    page: int
    limit: int
    data: List[RegistrationResponse]


# ============================================
# PAIEMENT : VÉRIFICATION OBLIGATOIRE DU REÇU (B9 / B11)
# ============================================

class RegistrationCreatedResponse(RegistrationResponse):
    # Jeton à conserver par l'inscrit : seul moyen d'envoyer son reçu (affiché une seule fois)
    verification_token: str


class PaymentStatusResponse(BaseModel):
    matricule: str
    statut: str   # "en_attente", "verifie", "rejete", "erreur", "verifie_historique"
    message: Optional[str] = None


class PaymentDetailResponse(PaymentStatusResponse):
    methode: Optional[str] = None
    verified_by: Optional[str] = None
    verified_at: Optional[datetime] = None
    transaction_id: Optional[str] = None
    historique: List[dict] = []


class ManualPaymentDecision(BaseModel):
    decision: Literal["verifie", "rejete"]
    transaction_id: Optional[Identifier] = None
    motif: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]

    @model_validator(mode="after")
    def transaction_required_for_validation(self):
        if self.decision == "verifie" and not self.transaction_id:
            raise ValueError("transaction_id est obligatoire pour valider un paiement")
        return self
