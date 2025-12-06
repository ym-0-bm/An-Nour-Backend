from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime


class PersonalInfo(BaseModel):
    nom: str
    prenom: str
    sexe: str
    age: str
    communeHabitation: str
    niveauAcademique: str
    communeAutre: Optional[str] = ""
    contactParent: str
    contactSeminariste: Optional[str] = ""


class DormitoryInfo(BaseModel):
    dortoir: str
    dortoirId: str


class HealthInfo(BaseModel):
    allergie: Optional[str] = "RAS"
    antecedentMedical: Optional[str] = "Néant"


class PaymentInfo(BaseModel):
    transactionId: str
    receiptUrl: Optional[str] = None
    amount: int  # Ne sera pas stocké en DB


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