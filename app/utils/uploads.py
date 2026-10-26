import io

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
# Au-delà, une image peut saturer la mémoire au décodage (bombe de décompression)
MAX_IMAGE_PIXELS = 40_000_000
_CHUNK_SIZE = 64 * 1024


async def read_image_upload(upload: UploadFile, max_bytes: int) -> bytes:
    """Lire une image téléversée en bornant sa taille et en vérifiant son contenu réel (pas l'en-tête du client)."""
    chunks, size = [], 0
    while chunk := await upload.read(_CHUNK_SIZE):
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Image trop volumineuse (maximum {max_bytes // (1024 * 1024)} Mo)"
            )
        chunks.append(chunk)

    data = b"".join(chunks)
    invalid = HTTPException(status_code=400, detail="Le fichier doit être une image JPEG, PNG ou WEBP valide")
    if not data:
        raise invalid

    try:
        with Image.open(io.BytesIO(data)) as image:
            image_format = image.format
            width, height = image.size
            image.verify()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise invalid

    if image_format not in ALLOWED_IMAGE_FORMATS or width * height > MAX_IMAGE_PIXELS:
        raise invalid

    return data
