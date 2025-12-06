# utils/ocr_processor.py
import io
import logging
import os
import re
import sys
import shutil
from typing import Tuple

import cv2
import numpy as np
from PIL import Image, ImageFile
import pytesseract

# PIL fixes (préexistants)
ImageFile.LOAD_TRUNCATED_IMAGES = True
Image.MAX_IMAGE_PIXELS = None

logger = logging.getLogger(__name__)


class OCRProcessor:
    """
    OCRProcessor : prétraitement robuste puis plusieurs stratégies Tesseract.
    Prétraitement obligatoire :
      - gris
      - crop marges blanches
      - augmentation contraste (CLAHE + stretch)
      - débruitage bilateral
      - sharpen
      - binarisation adaptative
      - deskew (tesseract OSD fallback -> contours)
    """

    def __init__(self):
        self.supported_languages = ["fra", "eng"]
        self._setup_tesseract_path()

        # réglages OCR de base
        self.base_oem = 3
        # whitelist inclut signes courants (F, €, CFA, ✓ etc).
        self.base_whitelist = (
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.,€$F-+:✓✔()[]éèêëàâäçôöûüùîïÉÈÊËÀÂÄÇÔÖÛÜÙÎÏ-–"
        )

    def _setup_tesseract_path(self):
        """Détecte Tesseract automatiquement, lève une exception si introuvable."""
        try:
            tesseract_path = shutil.which("tesseract")
            if tesseract_path:
                pytesseract.pytesseract.tesseract_cmd = tesseract_path
                logger.info(f"Tesseract trouvé dans PATH : {tesseract_path}")
            else:
                # tenter chemins usuels
                if sys.platform.startswith("linux"):
                    for p in ("/usr/bin/tesseract", "/usr/local/bin/tesseract"):
                        if os.path.exists(p):
                            pytesseract.pytesseract.tesseract_cmd = p
                            logger.info(f"Tesseract trouvé : {p}")
                            break
                elif sys.platform == "win32":
                    possible = [
                        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                    ]
                    for p in possible:
                        if os.path.exists(p):
                            pytesseract.pytesseract.tesseract_cmd = p
                            logger.info(f"Tesseract trouvé : {p}")
                            break
                elif sys.platform == "darwin":
                    for p in ("/usr/local/bin/tesseract", "/opt/homebrew/bin/tesseract"):
                        if os.path.exists(p):
                            pytesseract.pytesseract.tesseract_cmd = p
                            logger.info(f"Tesseract trouvé : {p}")
                            break

            # vérifie enfin
            version = pytesseract.get_tesseract_version()
            logger.info(f"Tesseract configuré : {version}")
        except Exception as e:
            raise Exception(
                "Tesseract non trouvé ou mal installé. Voir installation (apt/brew/UB-Mannheim) ; "
                "appendix: https://github.com/tesseract-ocr/tesseract"
            ) from e

    # -------------------------
    # Prétraitement d'image
    # -------------------------
    def _to_grayscale(self, img: np.ndarray) -> np.ndarray:
        if len(img.shape) == 3:
            return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        return img

    def _auto_crop_margins(self, gray: np.ndarray, pad: int = 20) -> np.ndarray:
        """
        Supprime larges marges blanches qui trompent l'OCR.
        On binarise légèrement et on prend le bounding box des zones noires.
        """
        # small blur to reduce tiny speckles
        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        # Threshold adaptatif pour s'adapter au fond gris clair
        th = cv2.adaptiveThreshold(
            blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 10
        )
        # Morph close pour regrouper texte
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 3))
        closed = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return gray  # rien à faire
        # union bbox
        xs, ys, ws, hs = zip(*(cv2.boundingRect(c) for c in contours))
        x1, y1 = max(min(xs) - pad, 0), max(min(ys) - pad, 0)
        x2, y2 = min(max(xs) + max(ws) + pad, gray.shape[1]), min(max(ys) + max(hs) + pad, gray.shape[0])
        if x2 - x1 < 50 or y2 - y1 < 50:
            return gray
        return gray[y1:y2, x1:x2]

    def _apply_clahe_and_stretch(self, gray: np.ndarray) -> np.ndarray:
        # CLAHE (local contrast)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        c = clahe.apply(gray)
        # contrast stretch (min-max)
        p2, p98 = np.percentile(c, (2, 98))
        if p98 - p2 > 0:
            stretched = np.clip((c - p2) * 255.0 / (p98 - p2), 0, 255).astype(np.uint8)
            return stretched
        return c

    def _denoise_and_sharpen(self, img: np.ndarray) -> np.ndarray:
        den = cv2.bilateralFilter(img, d=9, sigmaColor=75, sigmaSpace=75)
        # Unsharp mask
        blur = cv2.GaussianBlur(den, (0, 0), 2.0)
        sharp = cv2.addWeighted(den, 1.5, blur, -0.5, 0)
        return sharp

    def _adaptive_binarize(self, img: np.ndarray) -> np.ndarray:
        # blockSize pair/odd required; choose relatively large because text fine
        bin_img = cv2.adaptiveThreshold(
            img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 10
        )
        return bin_img

    def _deskew(self, gray: np.ndarray) -> np.ndarray:
        """
        Essaye Tesseract OSD puis fallback via contours / minAreaRect.
        """
        try:
            osd = pytesseract.image_to_osd(Image.fromarray(gray), config="--psm 0")
            m = re.search(r"Rotate: (\d+)", osd)
            if m:
                angle = int(m.group(1))
                if angle != 0 and angle != 360:
                    h, w = gray.shape[:2]
                    M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
                    return cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        except Exception:
            # fallback
            try:
                thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, 8)
                coords = np.column_stack(np.where(thresh > 0))
                if coords.size == 0:
                    return gray
                angle = cv2.minAreaRect(coords)[-1]
                if angle < -45:
                    angle = -(90 + angle)
                else:
                    angle = -angle
                if abs(angle) > 0.5:
                    h, w = gray.shape[:2]
                    M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
                    return cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
            except Exception:
                return gray
        return gray

    def _enhance_for_fine_font(self, gray: np.ndarray) -> np.ndarray:
        """
        Spécial pour police iOS très fine : on dilate légèrement les traits pour rendre
        les caractères plus visibles, puis on re-binarise.
        """
        # morphological dilation with very small kernel to thicken thin fonts
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 2))
        thick = cv2.dilate(gray, kernel, iterations=1)
        return thick

    def preprocess_image(self, pil_image: Image.Image) -> Image.Image:
        """Pipeline complet de prétraitement renvoyant une PIL.Image en mode 'L' ou 'RGB'"""
        try:
            img = np.array(pil_image.convert("RGB"))
            gray = self._to_grayscale(img)

            # 1) crop marges blanches importantes
            cropped = self._auto_crop_margins(gray, pad=20)

            # 2) deskew
            deskewed = self._deskew(cropped)

            # 3) contraste local + stretch
            enhanced = self._apply_clahe_and_stretch(deskewed)

            # 4) denoise + sharpen
            sharp = self._denoise_and_sharpen(enhanced)

            # 5) fine-font enhancement (slight dilation)
            thick = self._enhance_for_fine_font(sharp)

            # 6) binarisation adaptative finale
            bin_img = self._adaptive_binarize(thick)

            # 7) padding (évite qudes contours collent aux bords)
            final = cv2.copyMakeBorder(bin_img, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)

            return Image.fromarray(final).convert("L")
        except Exception as e:
            logger.warning(f"Prétraitement échoué ({e}), retour image originale convertie")
            return pil_image.convert("L")

    # -------------------------
    # OCR multi-stratégies
    # -------------------------
    def _tesseract_config(self, psm: int) -> str:
        return (
            f"--oem {self.base_oem} --psm {psm} "
            f"-c tessedit_char_whitelist={self.base_whitelist} "
            "-c load_system_dawg=0 -c load_freq_dawg=0 -c load_number_dawg=0"
        )

    def clean_extracted_text(self, text: str) -> str:
        if not text:
            return ""
        # normalisations courantes
        t = text.replace("\r\n", "\n").replace("\r", "\n")
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r"\n\s*\n", "\n\n", t)
        # common OCR corrections
        t = t.replace("XOF", "CFA").replace("CFAF", "CFA")
        # replace weird hyphens
        t = t.replace("–", "-").replace("—", "-")
        # try to fix common l/1/O/0 confusions in context (not globally)
        t = re.sub(r"(?<=\d)[\s,\.](?=\d{3}\b)", "", t)  # join thousand groups like '6.000' -> '6000' but keep correct separators elsewhere
        return t.strip()

    def process_image(self, image_content: bytes) -> str:
        """
        Exécute OCR multiple strategies et retourne le meilleur texte (le plus long utile).
        Prétraitement obligatoire.
        """
        try:
            pil = Image.open(io.BytesIO(image_content))
            if pil.mode != "RGB":
                pil = pil.convert("RGB")

            pre = self.preprocess_image(pil)

            strategies = [
                ("fra+eng", 11, "sparse"),
                ("fra+eng", 6, "single_block"),
                ("fra+eng", 4, "columns"),
                ("fra", 3, "complete"),
            ]

            candidates = []
            for langs, psm, name in strategies:
                try:
                    cfg = self._tesseract_config(psm)
                    text = pytesseract.image_to_string(pre, lang=langs, config=cfg)
                    text = self.clean_extracted_text(text or "")
                    if text and text.strip():
                        candidates.append((len(text), text, name))
                        logger.info(f"OCR strategy [{name}] -> {len(text)} chars")
                except Exception as e:
                    logger.debug(f"OCR strategy [{name}] failed: {e}")

            # fallback: OCR on original image (unprocessed) if none
            if not candidates:
                try:
                    cfg = self._tesseract_config(3)
                    text = pytesseract.image_to_string(pil, lang="fra+eng", config=cfg)
                    text = self.clean_extracted_text(text or "")
                    if text:
                        candidates.append((len(text), text, "fallback_original"))
                except Exception as e:
                    logger.error(f"Fallback OCR failed: {e}")

            if not candidates:
                raise Exception("Aucun texte détecté après toutes les stratégies OCR")

            best = max(candidates, key=lambda x: x[0])[1]
            logger.info(f"OCR réussi -> {len(best)} caractères retournés")
            return best
        except Exception as e:
            logger.exception("Échec OCR total")
            raise

    # Extraction structurée (utile pour debug / UI)
    def extract_text_structured(self, image_content: bytes) -> dict:
        try:
            pil = Image.open(io.BytesIO(image_content)).convert("RGB")
            pre = self.preprocess_image(pil)
            data = pytesseract.image_to_data(
                pre, lang="fra+eng", config=self._tesseract_config(11), output_type=pytesseract.Output.DICT
            )
            elements = []
            for i, txt in enumerate(data.get("text", [])):
                try:
                    conf = float(data["conf"][i])
                except Exception:
                    conf = 0.0
                if conf > 10 and txt.strip():
                    elements.append(
                        {
                            "text": txt.strip(),
                            "conf": conf,
                            "x": int(data.get("left", [0])[i]),
                            "y": int(data.get("top", [0])[i]),
                            "w": int(data.get("width", [0])[i]),
                            "h": int(data.get("height", [0])[i]),
                        }
                    )
            elements.sort(key=lambda e: (e["y"], e["x"]))
            full = " ".join(e["text"] for e in elements)
            return {"full_text": self.clean_extracted_text(full), "elements": elements, "count": len(elements)}
        except Exception as e:
            logger.exception("Extraction structurée échouée")
            return {"full_text": "", "elements": [], "count": 0}
