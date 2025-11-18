# utils/receipt_analyzer.py
import re
import logging
from typing import Optional, Dict, Any
from app.database import prisma

logger = logging.getLogger(__name__)


class ReceiptAnalyzer:
    def __init__(self):
        self.transaction_id_blacklist = {
            '1DDETRANSACTI0N', 'IDDETRANSACTI0N', 
            'ENPARTENARIATAVECUBA', 'TRANSACTIONID',
            'TRANSACTI0N1D'  # Nouveau: évite de capturer le label
        }

    def _clean_ocr(self, text: str) -> str:
        """Nettoyage OCR léger"""
        t = text.upper()
        t = re.sub(r'N[0O]UVEAU\s*S[0O][1I]DE', 'NOUVEAU SOLDE', t)
        t = re.sub(r'\s+', ' ', t)
        return t.strip()

    def _clean_account_name(self, raw_name: str) -> str:
        """Nettoie un nom de compte bruité par l'OCR"""
        # Corrections OCR communes dans les noms
        clean = raw_name.strip()
        clean = clean.replace('0', 'o').replace('1', 'l')
        clean = clean.replace('.', ' ').replace('_', ' ')
        clean = re.sub(r'\s+', ' ', clean).strip()
        return clean

    def extract_amount(self, raw_text: str) -> Optional[float]:

        pattern = r'(-?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)\s*F'
        
        candidates = []
        for m in re.finditer(pattern, raw_text, re.IGNORECASE):
            amount_str = m.group(1).strip()
            num = re.sub(r'[^\d]', '', amount_str)
            if num:
                val = int(num)
                if 500 <= val <= 500000:
                    candidates.append((m.start(), val))
        
        if not candidates:
            return None

        # ✅ CORRECTION: Ajouter [1] pour extraire seulement le montant
        amount = min(candidates, key=lambda x: x[0])[1]  # [1] = deuxième élément du tuple
        logger.debug(f"Montant extrait : {amount}F")
        return float(amount)

    def extract_transaction_id(self, raw_text: str) -> Optional[str]:
        """
        Extrait l'ID de transaction (supporte T., T_, TZ, etc.)
        Exemple: T.V4FVURZFR0PGF3MQ → TV4FVURZFR0PGF3MQ
        """
        clean = self._clean_ocr(raw_text)
        
        # Patterns par priorité (du plus spécifique au plus général)
        patterns = [
            # Format avec séparateur: T.XXXX ou T_XXXX ou T XXXX
            r'T[._\s]([A-Z0-9]{14,19})',
            
            # Format standard: TZXXXX ou TEXXXX
            r'\b(T[ZEH][A-Z0-9]{13,18})\b',
            
            # Après label "Transaction ID" (accepte TRANSACTI0N1D)
            r'TRANSACTI[0O]N[\s]*[1I]D[\s:]*T?[._\s]?([A-Z0-9]{14,19})',
            
            # Fallback général
            r'\b([TV][A-Z0-9]{14,19})\b',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, clean)
            if match:
                tid = match.group(1) if match.lastindex else match.group(0)
                
                # Nettoie le résultat
                tid = tid.replace('.', '').replace('_', '').replace(' ', '')
                
                # Corrections OCR sur l'ID
                tid = tid.replace('I', '1').replace('O', '0').replace('l', '1')
                
                # Ajoute T au début si manquant
                if not tid.startswith('T'):
                    tid = 'T' + tid
                
                # Vérifie blacklist et longueur
                if tid not in self.transaction_id_blacklist and 15 <= len(tid) <= 20:
                    logger.debug(f"ID transaction : {tid}")
                    return tid
        
        return None

    def extract_account_name(self, raw_text: str) -> Optional[str]:
        """
        Extrait le nom du compte (gère OCR très bruité)
        Exemple: S01dt0Axe1.K. → Axel K
        """
        text_upper = raw_text.upper()
        
        # Format anglais avec OCR bruité: "S01dt0" = "Sold to"
        # Pattern ultra-flexible pour gérer tous les cas de figure
        patterns = [
            # Version bruitée: S01dt0, S0ldt0, etc.
            r'S[0O][1Il]D\s*T[0O]\s*([A-Z0-9][A-Z0-9.\s]{2,30}?)(?=\n|NET|WAVE|N[E3]T|AM[0O]UNT)',
            
            # Version propre: Sold to
            r'SOLD\s+TO\s+([A-Z][A-Z\s]{2,30}?)(?=\n|NET|WAVE|$)',
            
            # Format français: Paiement
            r'PA[1I][E3]M[E3]NT\s*([A-Z0-9]{3,25})',
            r'PAIEMENT\s*([A-Z0-9]{3,25})',
            
            # Format français: De
            r'D[E3]\s+([A-Z][A-Z0-9\s]+?)\s+T\s+\d',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text_upper)
            if match:
                raw_name = match.group(1).strip()
                
                # Nettoie le nom
                clean_name = self._clean_account_name(raw_name)
                
                # Enlève les suffixes numériques (K2MI10 → K2MI)
                clean_name = re.sub(r'\d+$', '', clean_name)
                
                if len(clean_name) >= 2:
                    logger.debug(f"Nom extrait: '{raw_name}' → '{clean_name}'")
                    return clean_name.title()
        
        # Fallback: noms connus (avec normalisation stricte)
        known = {
            'K2MILO': 'K2Milo',
            'K2MILO': 'K2Milo',  # Variantes possibles
            'K2MI': 'K2Milo',    # K2MI10 → K2MI → K2Milo
        }
        
        for key, proper in known.items():
            if key in text_upper:
                logger.debug(f"Nom trouvé (fallback): {proper}")
                return proper
        
        return None

    def check_status(self, raw_text: str) -> bool:
        """
        Vérifie le statut (ultra-flexible pour OCR pourri)
        Accepte: Completed, C0mp1eted, Effectué, etc.
        """
        patterns = [
            # Anglais avec variations OCR: C0mp1eted, Comp1eted, etc.
            r'C[0O]MP[1LI][E3]T[E3]D',
            
            # Avec symbole checkmark
            r'[✓✔]\s*C[0O]MP[1LI][E3]T[E3]D',
            
            # Après label Status
            r'STATUS[\s.:]*[✓✔]?\s*C[0O]MP[1LI][E3]T[E3]D',
            
            # Français avec variations
            r'[E3]FF[E3]CTU[ÉE]',
            r'STATUT[\s.:]*[E3]FF[E3]CTU[ÉE]',
            
            # Version propre (fallback)
            r'COMPLETED',
            r'EFFECTU[ÉE]',
        ]
        
        for pattern in patterns:
            if re.search(pattern, raw_text, re.IGNORECASE):
                logger.debug(f"Statut validé avec pattern: {pattern[:40]}")
                return True
        
        return False

    def check_wave(self, raw_text: str) -> bool:
        """
        Vérifie la signature Wave (gère texte collé et OCR bruité)
        Accepte: WaveFeeAmount, WaveFeeAm0unt, Wave Fee Amount, etc.
        """
        clean = self._clean_ocr(raw_text)
        
        markers = [
            # Wave Fee collé ou séparé
            r'WAVEFEE',
            r'WAVE\s*FEE',
            r'WAVE.*AM[0O]UNT',
            r'WAVE.*F[E3][E3]',
            
            # Android français
            r'PARTENARIAT.*UBA',
            r'UBA',
            
            # Autres marqueurs
            r'NET\s*AM[0O]UNT',
            r'NETAM[0O]UNT',
        ]
        
        for marker in markers:
            if re.search(marker, clean, re.IGNORECASE):
                logger.debug(f"Signature Wave trouvée: {marker}")
                return True
        
        return False
    def get_user_message(self, is_valid: bool, errors: list, score: int,
        account_name: str, amount: float, expected_amount: float, transaction_id_exist) -> str:
        """Génère un message clair et simple pour l'utilisateur"""
        # ✅ Paiement valide
        if is_valid:
            return "✓ Paiement vérifié avec succès !"

        # ❌ Image complètement illisible (score très bas)
        if score < 30:
            return "Image floue ou illisible. Prenez une capture d'écran plus claire et réessayez."

        # ❌ Cas spécifiques (1 seule erreur majeure)
        if len(errors) == 1:
            error = errors
            
            if "Signature Wave" in error:
                return "Ceci n'est pas un reçu Wave. Envoyez une capture depuis votre application Wave."
            
            if "ID de transaction" in error:
                return "Impossible de lire le numéro de transaction. Prenez une photo plus nette."
            
            if "Statut" in error:
                return "Le statut du paiement est illisible. Vérifiez que la capture montre un paiement effectué."

        # ❌ ID Transaction déja existant
        if transaction_id_exist :
            return "Cette transaction a déjà été utilisée pour une autre inscription"

        # ❌ Montant incorrect (erreur prioritaire)
        if any("Montant incorrect" in e for e in errors):
            if amount:
                return f"Montant invalide : vous avez payé {int(amount)}F, mais {int(expected_amount)}F sont requis."
            else:
                return f"Le montant du paiement est illisible. Le montant attendu est {int(expected_amount)}F."

        # ❌ Mauvais destinataire (erreur prioritaire)
        if any("Nom du compte invalide" in e for e in errors):
            if account_name:
                return f"Destinataire incorrect : vous avez payé '{account_name}', mais le paiement doit être fait à 'K2Milo'."
            else:
                return "Impossible d'identifier le destinataire. Le paiement doit être envoyé à 'K2Milo'."

        # ❌ Cas général (multiples erreurs)
        return "Cette capture n'est pas une preuve de paiement valide. Validation refusée."

    async def analyze_receipt(self, extracted_text: str, expected_amount: float) -> Dict[str, Any]:
        """Analyse complète avec validation stricte"""
        logger.info("Analyse reçu Wave (OCR multilingue + très bruité)")
        
        # Extractions
        amount = self.extract_amount(extracted_text)
        transaction_id = self.extract_transaction_id(extracted_text)
        account_name = self.extract_account_name(extracted_text)
        status_ok = self.check_status(extracted_text)
        is_wave = self.check_wave(extracted_text)
        
        # Validations strictes
        amount_ok = amount is not None and amount == expected_amount
        name_ok = account_name in['K2Milo',"K2MILO",'K2Milo', 'K2Milo',  'K2MI']

        # Vérifier que la transctionId est unique dans la base de données
        transaction_id_exist = await prisma.registration.find_first(
            where={"transaction_id": transaction_id}
        )

        # Validation globale
        is_valid = bool(not transaction_id_exist and amount_ok and status_ok and is_wave and name_ok)
        
        # Score cohérent (total = 100)
        score = 0
        if transaction_id: score += 30
        if amount_ok: score += 30
        if name_ok: score += 20
        if status_ok: score += 15
        if is_wave: score += 5
        
        # Erreurs techniques (pour les logs)
        errors = []
        if not transaction_id:
            errors.append("ID de transaction manquant ou invalide")
        if not amount_ok:
            errors.append(f"Montant incorrect : {amount or '?'}F ≠ {expected_amount}F")
        if not status_ok:
            errors.append("Statut non confirmé (Completed/Effectué)")
        if not is_wave:
            errors.append("Signature Wave manquante")
        if not name_ok:
            errors.append(f"Nom du compte invalide : '{account_name}' (doit être 'K2Milo')")
        if transaction_id_exist:
            errors.append(f"Transaction id exist : {transaction_id}")
        # ✅ Message simplifié pour l'utilisateur
        user_message = self.get_user_message(is_valid, errors, score, account_name, amount, expected_amount, transaction_id_exist)
        
        return {
            "isValid": is_valid,
            "score": score,
            "message": user_message,  # ✅ Message simple et clair
            "extractedData": {
                "amount": amount,
                "transactionId": transaction_id,
                "accountName": account_name,
                "status": "Completed" if status_ok else None
            },
            "checks": {
                "hasTransactionId": bool(transaction_id),
                "hasCorrectAmount": amount_ok,
                "hasStatus": status_ok,
                "isWaveReceipt": is_wave,
                "hasValidAccountName": name_ok
            },
            "errors": errors,  # Garde les erreurs techniques pour debug (optionnel)
            "warnings": [],
            "debugText": extracted_text[:1000]
        }
   