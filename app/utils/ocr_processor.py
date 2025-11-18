# utils/ocr_processor.py
import pytesseract
from PIL import Image, ImageFile
import cv2
import numpy as np
import io
import logging
import re
import os
import sys

# ===================================================================
# CORRECTIFS PILLOW - ÉLIMINE L'ERREUR "image file is truncated"
# ===================================================================
ImageFile.LOAD_TRUNCATED_IMAGES = True   # Ligne magique indispensable
Image.MAX_IMAGE_PIXELS = None            # Pour les très gros screenshots
# ===================================================================

logger = logging.getLogger(__name__)


class OCRProcessor:
    def __init__(self):
        self.supported_languages = ['fra', 'eng']
        self._setup_tesseract_path()


    # Ajouter cette ligne au début de __init__
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    def _setup_tesseract_path(self):
        """Configuration automatique du chemin Tesseract"""
        try:
            if sys.platform == "win32":
                possible_paths = [
                    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                    r"C:\Users\{}\AppData\Local\Tesseract-OCR\tesseract.exe".format(os.getenv('USERNAME')),
                    r"C:\tesseract\tesseract.exe"
                ]
                for path in possible_paths:
                    if os.path.exists(path):
                        pytesseract.pytesseract.tesseract_cmd = path
                        logger.info(f"Tesseract trouvé : {path}")
                        break

            elif sys.platform == "darwin":
                for path in ["/usr/local/bin/tesseract", "/opt/homebrew/bin/tesseract"]:
                    if os.path.exists(path):
                        pytesseract.pytesseract.tesseract_cmd = path
                        logger.info(f"Tesseract trouvé : {path}")
                        break

            version = pytesseract.get_tesseract_version()
            logger.info(f"Tesseract {version} configuré avec succès")

        except Exception as e:
            raise Exception(
                "Tesseract non trouvé ou mal installé !\n"
                "→ Windows : https://github.com/UB-Mannheim/tesseract/wiki\n"
                "→ macOS : brew install tesseract tesseract-lang\n"
                "→ Linux : sudo apt install tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng"
            ) from e

    def _deskew_image(self, gray: np.ndarray) -> np.ndarray:
        """Correction automatique de l'inclinaison"""
        try:
            osd = pytesseract.image_to_osd(Image.fromarray(gray), config='--psm 0')
            angle = int(re.search(r'Rotate: (\d+)', osd).group(1))
            if angle and angle != 360:
                h, w = gray.shape[:2]
                M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
                gray = cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
                logger.debug(f"Deskew appliqué : {angle}°")
        except Exception as e:
            logger.debug(f"Deskew ignoré : {e}")
        return gray


    def preprocess_image(self, image: Image.Image) -> Image.Image:
        """Prétraitement ultra-efficace pour captures Wave"""
        try:
            logger.info("Prétraitement de l'image...")

            arr = np.array(image)
            h, w = arr.shape[:2]

            # 1. Upscale si trop petit
            if w < 1000:
                scale = max(2.0, 1500 / w)
                arr = cv2.resize(arr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)

            # 2. Gris + deskew 
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY) if len(arr.shape) == 3 else arr
            gray = self._deskew_image(gray)

            # 3. Débruitage + contraste
            denoised = cv2.bilateralFilter(gray, 9, 75, 75)
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(denoised)

            # 4. Affûtage
            blur = cv2.GaussianBlur(enhanced, (0, 0), 2.0)
            sharpened = cv2.addWeighted(enhanced, 1.8, blur, -0.8, 0)

            # 5. Binarisation
            binary = cv2.adaptiveThreshold(sharpened, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                           cv2.THRESH_BINARY, 15, 7)

            # 6. Nettoyage + bordure
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
            cleaned = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
            final = cv2.copyMakeBorder(cleaned, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)

            result = Image.fromarray(final)
            logger.info("Prétraitement terminé")
            return result

        except Exception as e:
            logger.warning(f"Prétraitement échoué ({e}), retour image originale")
            return image.convert("RGB") if image.mode != "RGB" else image

    def process_image(self, image_content: bytes) -> str:
        """OCR ultra-robuste avec plusieurs stratégies"""
        try:
            logger.info("Début OCR avancé...")

            # Chargement image
            image = Image.open(io.BytesIO(image_content))
            if image.mode != "RGB":
                image = image.convert("RGB")

            processed = self.preprocess_image(image)

            # Config de base
            base_config = (
                "--oem 3 --dpi 300 "
                "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.,€$F-+:✓✔()[]éèêëàâäçôöûüùîïÉÈÊËÀÂÄÇÔÖÛÜÙÎÏ "
                "-c load_system_dawg=0 -c load_freq_dawg=0 -c load_number_dawg=0"
            )

            candidates = []

            # Stratégies OCR (PSM 11 = meilleur pour tout récupérer)
            strategies = [
                ("fra+eng", "--psm 11", "Sparse text (meilleur taux de récupération)"),
                ("fra+eng", "--psm 4",  "Colonne unique"),
                ("fra",     "--psm 6",  "Bloc uniforme"),
                ("fra+eng", "--psm 3",  "Auto complet"),
            ]

            for lang, psm, name in strategies:
                config = f"{base_config} {psm}"
                try:
                    text = pytesseract.image_to_string(processed, lang=lang, config=config)
                    if text and text.strip():
                        cleaned = self.clean_extracted_text(text)
                        candidates.append((len(cleaned), cleaned, name))
                        logger.info(f"{name} → {len(cleaned)} caractères")
                except Exception as e:
                    logger.debug(f"{name} échoué : {e}")

            # Fallback image originale
            if not candidates:
                logger.info("Fallback → image originale")
                text = pytesseract.image_to_string(image, lang="fra+eng", config="--oem 3 --psm 3")
                cleaned = self.clean_extracted_text(text or "")
                if cleaned.strip():
                    candidates.append((len(cleaned), cleaned, "Fallback original"))

            if not candidates:
                raise Exception("Aucun texte détecté")

            # Meilleur résultat
            best_text = max(candidates, key=lambda x: x[0])[1]
            logger.info(f"OCR réussi → {len(best_text)} caractères")
            return best_text

        except Exception as e:
            logger.error(f"Échec OCR total : {e}", exc_info=True)
            raise Exception(f"Impossible d'extraire le texte de l'image : {str(e)}")

    def clean_extracted_text(self, text: str) -> str:
        """Nettoyage agressif + corrections Wave"""
        if not text:
            return ""

        text = text.replace('\r\n', '\n').replace('\r', '\n')
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n\s*\n', '\n\n', text)

        # Corrections fréquentes
        corrections = {
            'Palement': 'Paiement', 'Effectue': 'Effectué', 'Recu': 'Reçu',
            'envoye': 'envoyé', 'Depot': 'Dépôt', 'Retrait': 'Retrait',
            'XOF': 'CFA', 'CFAF': 'CFA'
        }
        for bad, good in corrections.items():
            text = re.sub(rf'\b{bad}\b', good, text, flags=re.IGNORECASE)

        # Montants
        text = re.sub(r'(\d)\s+(\d)', r'\1\2', text)        # 1 000 → 1000
        text = re.sub(r'(\d)\s+F\b', r'\1F', text)         # 500 F → 500F
        text = text.replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1")

        # Accents
        text = text.replace("e'", "é").replace("c,", "ç")

        return text.strip()

    def extract_text_structured(self, image_content: bytes) -> dict:
        """Avec positions (pour debug/parsing)"""
        try:
            image = Image.open(io.BytesIO(image_content)).convert("RGB")
            processed = self.preprocess_image(image)

            data = pytesseract.image_to_data(
                processed, lang="fra+eng",
                config="--oem 3 --psm 11 --dpi 300 -c load_system_dawg=0",
                output_type=pytesseract.Output.DICT
            )

            elements = []
            for i in range(len(data["text"])):
                if int(data["conf"][i]) > 10 and data["text"][i].strip():
                    elements.append({
                        "text": data["text"][i].strip(),
                        "conf": int(data["conf"][i]),
                        "x": data["left"][i],
                        "y": data["top"][i],
                        "w": data["width"][i],
                        "h": data["height"][i]
                    })

            elements.sort(key=lambda x: (x["y"], x["x"]))
            full = " ".join(e["text"] for e in elements)

            return {
                "full_text": self.clean_extracted_text(full),
                "elements": elements,
                "count": len(elements)
            }
        except Exception as e:
            logger.error(f"Extraction structurée échouée : {e}")
            return {"full_text": self.process_image(image_content), "elements": [], "count": 0}
