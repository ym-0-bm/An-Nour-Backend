from pydantic import BaseModel, Field, StringConstraints, field_validator
from typing import Annotated, Optional, List
from datetime import datetime

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