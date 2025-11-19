services:
  - type: web
    name: annour-backend
    env: python
    region: frankfurt
    plan: free
    branch: main
    buildCommand: |
      echo "🔧 Installing system dependencies..."
      apt-get update -qq
      apt-get install -y -qq \
        tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng \
        libgl1 libglib2.0-0 libsm6 libxrender1 libxext6

      echo "📦 Upgrading pip..."
      pip install --upgrade pip --quiet

      echo "📚 Installing Python packages..."
      pip install -r requirements.txt --quiet

      echo "🔨 Generating Prisma client..."
      prisma generate

      echo "✅ Build complete!"
    startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1
    healthCheckPath: /health
    envVars:
      - key: DATABASE_URL
        sync: false
      - key: API_V1_STR
        value: /api/v1
      - key: PROJECT_NAME
        value: Inscription System An-Nour
      - key: MEDIA_DIR
        value: media
      - key: PYTHON_VERSION
        value: 3.11.9
    autoDeploy: true
