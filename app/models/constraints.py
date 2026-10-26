"""Types de chaînes bornées pour les entrées publiques (espaces superflus retirés)."""
from typing import Annotated

from pydantic import StringConstraints

# Champ obligatoire court : nom, prénom, commune, niveau...
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
# Champ facultatif court (le champ reste Optional dans le schéma)
OptionalShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]
# Numéro de téléphone ou contact
Contact = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
OptionalContact = Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)]
# Texte libre (commentaires, informations médicales)
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
# Identifiant technique (transaction, code dortoir)
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Url = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2048)]
