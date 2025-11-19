#!/usr/bin/env bash
set -e

echo "🔧 Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng

echo "📦 Upgrading pip..."
pip install --upgrade pip --quiet

echo "📚 Installing Python packages..."
pip install -r requirements.txt --quiet

echo "🔨 Generating Prisma client..."
prisma generate

echo "✅ Build complete!"
