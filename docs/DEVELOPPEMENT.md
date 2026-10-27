# Développement local du backend

## Prérequis

- Python 3.11 (production, CI, Docker) — 3.13 fonctionne aussi en local.
- MongoDB 8 en local pour les tests d'intégration (replica set requis par Prisma).
- Tesseract (OCR) : `C:\Program Files\Tesseract-OCR` sous Windows, `tesseract-ocr` sous Linux.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # Windows (Linux : .venv/bin/python)
cp .env.example .env                                           # puis renseigner les valeurs
```

## Générer le client Prisma

```bash
prisma generate
```

### Windows : erreur `spawn prisma-client-py ENOENT`

Sur certains postes Windows, la CLI Node de Prisma ne parvient pas à lancer le générateur
`prisma-client-py`, même s'il est dans le `PATH`. Contournement **sans modifier le schéma du dépôt**
(le CI et Docker, sous Linux, ne sont pas concernés) : générer depuis une copie du schéma qui appelle
le générateur via Python.

```bash
mkdir -p /tmp/prisma-gen
sed 's/provider             = "prisma-client-py"/provider             = "python -m prisma"/' \
    prisma/schema.prisma > /tmp/prisma-gen/schema.prisma
export PATH="$(pwd)/.venv/Scripts:$PATH"
.venv/Scripts/python -m prisma generate --schema /tmp/prisma-gen/schema.prisma
```

Ne jamais committer la copie : `prisma/schema.prisma` reste la source de vérité.

## Tests

```bash
pytest                       # tests unitaires/API (Prisma simulé), avec couverture
```

Tests d'intégration (vraie base locale, garde-fou : hôte local et nom de base contenant `test`) :

```bash
mongod --dbpath <dossier> --port 27117 --replSet rs0
mongosh --port 27117 --eval 'rs.initiate({_id:"rs0", members:[{_id:0, host:"127.0.0.1:27117"}]})'
DATABASE_URL="mongodb://127.0.0.1:27117/annour_test?replicaSet=rs0&directConnection=true" \
    python -m prisma db push --skip-generate
INTEGRATION_DATABASE_URL="mongodb://127.0.0.1:27117/annour_test?replicaSet=rs0&directConnection=true" pytest
```

Tests de bout en bout (API dans son image Docker) : voir `tests/e2e/README.md`.
