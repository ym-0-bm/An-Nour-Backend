FROM python:3.11-slim

# Installer dépendances système
RUN apt-get update && \
    apt-get install -y tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Créer un utilisateur non-root pour la sécurité
RUN useradd -m -u 1000 appuser

# Installer dépendances Python
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier le projet
COPY . .

# Créer dossiers media avec permissions
RUN mkdir -p /app/media/participants_photos && \
    chown -R appuser:appuser /app

# Générer Prisma
RUN prisma generate

# Changer vers utilisateur non-root
USER appuser

# Exposer
EXPOSE 8000

# Commande de démarrage
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
