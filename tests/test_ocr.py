"""B22 : OCR réel (Tesseract) sur un reçu fictif généré ; ignoré si Tesseract n'est pas installé."""
import asyncio
import io
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.utils import receipt_analyzer
from app.utils.ocr_processor import OCRProcessor

pytesseract = pytest.importorskip("pytesseract")
try:
    OCRProcessor()  # localise Tesseract (PATH ou emplacements Windows usuels)
    pytesseract.get_tesseract_version()
except Exception:  # pragma: no cover - dépend du poste
    pytest.skip("Tesseract non installé", allow_module_level=True)

LIGNES = ["-6.000F", "Paiement AN-NOUR", "Statut Effectue", "En partenariat avec UBA", "ID de transaction",
          "TABCDEFGHJKLMNPQR"]


def receipt_image(fmt="PNG"):
    image = Image.new("RGB", (900, 80 * len(LIGNES) + 40), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=44)
    for i, ligne in enumerate(LIGNES):
        draw.text((30, 20 + i * 80), ligne, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
def test_ocr_reads_generated_receipt(fmt):
    text = OCRProcessor().process_image(receipt_image(fmt))
    compact = text.replace(" ", "").upper()
    assert "AN-NOUR" in compact
    assert "TABCDEFGHJKLMNPQR" in compact


def test_ocr_then_analysis_validates_receipt(monkeypatch):
    async def none(**kwargs):
        return None

    monkeypatch.setattr(receipt_analyzer.prisma, "registration", SimpleNamespace(find_first=none))
    monkeypatch.setattr(receipt_analyzer.prisma, "usedtransaction", SimpleNamespace(find_unique=none))
    text = OCRProcessor().process_image(receipt_image())
    result = asyncio.run(receipt_analyzer.ReceiptAnalyzer().analyze_receipt(text, 6000))
    assert result["extractedData"]["amount"] == 6000.0
    assert result["extractedData"]["transactionId"] == "TABCDEFGHJKLMNPQR"
    assert result["isValid"] is True, result["errors"]


def test_ocr_on_blank_image_raises():
    # Aucun texte : exception, transformée en statut « erreur » par la route d'envoi du reçu
    buffer = io.BytesIO()
    Image.new("RGB", (400, 200), "white").save(buffer, format="PNG")
    with pytest.raises(Exception, match="Aucun texte"):
        OCRProcessor().process_image(buffer.getvalue())
