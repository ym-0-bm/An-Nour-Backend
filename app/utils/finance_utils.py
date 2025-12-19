"""
Utilitaires pour le module finance.
Fonctions réutilisables pour créer des transactions automatiquement.
"""

from datetime import datetime
import uuid
from app.database import prisma


def generate_reference(type: str) -> str:
    """Générer référence unique transaction"""
    prefix = "ENT" if type == "ENTREE" else "SOR"
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    unique = str(uuid.uuid4())[:8].upper()
    return f"{prefix}-{timestamp}-{unique}"


async def create_inscription_entry(
    matricule: str,
    nom: str,
    prenom: str,
    montant: float = 6000.0,
    registration_date: datetime = None,
    mode_paiement: str = "Wave"
) -> dict:
    """
    Créer automatiquement une entrée finance pour une inscription.
    
    Cette fonction peut être appelée depuis n'importe quel module
    sans affecter le flux principal.
    
    Args:
        matricule: Matricule du séminariste
        nom: Nom du séminariste
        prenom: Prénom du séminariste
        montant: Montant de l'inscription (défaut: 6000 FCFA)
        registration_date: Date d'inscription (défaut: maintenant)
        mode_paiement: Mode de paiement (défaut: Wave)
    
    Returns:
        dict avec le résultat de la création
    """
    
    try:
        # Vérifier si déjà synchronisée
        existing = await prisma.transaction.find_first(
            where={
                "categorie": "Inscription",
                "matricule": matricule
            }
        )
        
        if existing:
            return {
                "success": True,
                "already_exists": True,
                "reference": existing.reference,
                "matricule": matricule
            }
        
        # Générer référence unique
        reference = generate_reference("ENTREE")
        date = registration_date or datetime.now()
        
        # Créer l'entrée finance
        transaction = await prisma.transaction.create(
            data={
                "reference": reference,
                "type": "ENTREE",
                "categorie": "Inscription",
                "montant": montant,
                "libelle": f"Inscription séminariste {nom} {prenom}",
                "description": f"Inscription au séminaire - Matricule: {matricule}",
                "payeur": f"{nom} {prenom}",
                "matricule": matricule,
                "mode_paiement": mode_paiement,
                "date_transaction": date,
                "created_by": "system",
                "statut": "validee"
            }
        )
        
        return {
            "success": True,
            "already_exists": False,
            "reference": transaction.reference,
            "matricule": matricule,
            "montant": montant
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "matricule": matricule
        }
