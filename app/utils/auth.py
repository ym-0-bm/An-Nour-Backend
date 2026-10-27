from datetime import datetime, timedelta, timezone
from typing import Optional
import re
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import hashlib

from app.config import settings

# Configuration
SECRET_KEY = settings.SECRET_KEY
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES

# Rôles reconnus (valeurs canoniques enregistrées en base)
ROLE_ADMIN = "admin"
ROLE_SCIENTIFIQUE = "scientifique"
ROLE_FINANCES = "finances"
ROLE_SANTE = "sante"
ROLES = (ROLE_ADMIN, ROLE_SCIENTIFIQUE, ROLE_FINANCES, ROLE_SANTE)

# Anciennes valeurs encore possibles en base ou envoyées par le frontend → valeur canonique
ROLE_ALIASES = {
    "administration": ROLE_ADMIN,
    "finance": ROLE_FINANCES,
    "scientific": ROLE_SCIENTIFIQUE,
    "santé": ROLE_SANTE,
}

OBJECT_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{24}$")

# Hash des mots de passe : argon2id (mêmes paramètres et même format que l'ancien passlib,
# les hash existants restent valides)
password_hasher = PasswordHasher()

# Bearer token (auto_error=False : un token absent renvoie 401 et non 403)
security = HTTPBearer(auto_error=False)


def normalize_role(role: Optional[str]) -> Optional[str]:
    """Ramener un rôle (ancienne valeur, casse différente) à sa valeur canonique"""
    if role is None:
        return None
    value = role.strip().lower()
    return ROLE_ALIASES.get(value, value)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Vérifier un mot de passe"""
    sha = hashlib.sha256(plain_password.encode("utf-8")).hexdigest()
    try:
        return password_hasher.verify(hashed_password, sha)
    except (VerificationError, InvalidHashError):
        return False


def get_password_hash(password: str) -> str:
    """Hasher un mot de passe"""
    # Pré-hash SHA-256 → longueur fixe
    sha = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return password_hasher.hash(sha)


def token_version(user) -> int:
    """Version de session de l'utilisateur (absente = 0, cas des comptes existants)."""
    return getattr(user, "token_version", None) or 0


async def revoke_sessions(user) -> None:
    """B19 : invalide immédiatement tous les tokens déjà émis pour cet utilisateur."""
    from app.database import prisma
    await prisma.user.update(where={"id": user.id}, data={"token_version": token_version(user) + 1})


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Créer un JWT token"""
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))

    to_encode.update({"exp": expire, "iat": now})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def decode_token(token: str) -> dict:
    """Décoder un JWT token"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError:
        raise _unauthorized("Token invalide ou expiré")


async def get_current_user(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
):
    """Récupérer l'utilisateur depuis le token"""
    from app.database import prisma

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("Authentification requise")

    token = credentials.credentials
    payload = decode_token(token)

    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not OBJECT_ID_PATTERN.match(user_id):
        raise _unauthorized("Token invalide")

    user = await prisma.user.find_unique(where={"id": user_id})
    if user is None:
        raise _unauthorized("Utilisateur non trouvé")

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte désactivé"
        )

    # B19 : un token émis avant une révocation (mot de passe, rôle, désactivation...) est refusé
    if payload.get("ver", 0) != token_version(user):
        raise _unauthorized("Session expirée, veuillez vous reconnecter")

    # Le rôle fait foi depuis la base (jamais depuis le token), sous sa forme canonique
    return user.model_copy(update={"role": normalize_role(user.role)})


def require_role(required_roles: list):
    """Vérifier que l'utilisateur a le bon rôle"""

    async def role_checker(current_user=Depends(get_current_user)):
        if current_user.role not in required_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accès refusé"
            )
        return current_user

    # Exposé pour les tests qui vérifient la matrice des droits
    role_checker.allowed_roles = tuple(required_roles)
    return role_checker


# Alias pour les rôles courants
RequireAdmin = Depends(require_role([ROLE_ADMIN]))
RequireScientifique = Depends(require_role([ROLE_ADMIN, ROLE_SCIENTIFIQUE]))
RequireFinances = Depends(require_role([ROLE_ADMIN, ROLE_FINANCES]))
RequireLectureSeminaristes = Depends(require_role([ROLE_ADMIN, ROLE_SCIENTIFIQUE, ROLE_SANTE]))
# Données médicales : décision Partie 6, accès complet pour sante et admin
RequireSante = Depends(require_role([ROLE_ADMIN, ROLE_SANTE]))
RequireAny = Depends(get_current_user)
