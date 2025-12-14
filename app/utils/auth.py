from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import hashlib
import os

# Configuration
SECRET_KEY = os.getenv("SECRET_KEY", "votre-cle-secrete-super-longue-et-complexe-a-changer")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 heures

# Hash passwords
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# Bearer token
security = HTTPBearer()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Vérifier un mot de passe"""
    sha = hashlib.sha256(plain_password.encode("utf-8")).hexdigest()
    return pwd_context.verify(sha, hashed_password)


def get_password_hash(password: str) -> str:
    """Hasher un mot de passe"""
    # Pré-hash SHA-256 → longueur fixe
    sha = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return pwd_context.hash(sha)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Créer un JWT token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> dict:
    """Décoder un JWT token"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide ou expiré",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
        credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Récupérer l'utilisateur depuis le token"""
    from app.database import prisma

    token = credentials.credentials
    payload = decode_token(token)

    user_id: str = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide"
        )

    user = await prisma.user.find_unique(where={"id": user_id})
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utilisateur non trouvé"
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte désactivé"
        )

    return user


def require_role(required_roles: list):
    """Vérifier que l'utilisateur a le bon rôle"""

    async def role_checker(current_user=Depends(get_current_user)):
        if current_user.role not in required_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Accès refusé. Rôle requis: {', '.join(required_roles)}"
            )
        return current_user

    return role_checker


# Alias pour les rôles courants
RequireAdmin = Depends(require_role(["admin"]))
RequireScientifique = Depends(require_role(["admin", "scientifique"]))
RequireFinances = Depends(require_role(["admin", "finances"]))
RequireAny = Depends(get_current_user)