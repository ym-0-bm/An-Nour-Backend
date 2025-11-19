FROM python:3.11-slim

# Installer dépendances système
RUN apt-get update && \
    apt-get install -y tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Installer dépendances Python
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier le projet
COPY . .

# Générer Prisma
RUN prisma generate

# Exposer
EXPOSE 8000

# Commande de démarrage
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
