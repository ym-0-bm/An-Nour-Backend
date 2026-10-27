FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Moteur Prisma dans un dossier fixe, lisible par l'utilisateur d'exécution (non-root)
    PRISMA_BINARY_CACHE_DIR=/opt/prisma-cache

# Installer dépendances système
RUN apt-get update && \
    apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Utilisateur d'exécution sans privilèges (B18)
RUN useradd --system --uid 10001 --create-home --home-dir /home/app app

# Installer dépendances Python
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier le projet (le code reste la propriété de root : lecture seule pour l'application)
COPY . .

# Générer Prisma, puis donner le moteur à l'utilisateur d'exécution
RUN prisma generate && chown -R app:app /opt/prisma-cache

USER app

# Exposer
EXPOSE 8000

# Commande de démarrage
# --no-access-log : le journal d'accès d'uvicorn inclut la chaîne de requête (données personnelles
# possibles) ; l'application journalise les accès sans elle (app/logging_config.py)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
