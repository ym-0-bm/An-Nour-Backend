#!/usr/bin/env bash
# Sauvegarde chiffrée de la base (B15) : mongodump compressé, chiffré pour la clé publique.
# Variables requises : BACKUP_DATABASE_URL (idéalement un utilisateur Atlas en lecture seule),
#                      BACKUP_CERT_PEM (contenu de backup_public_cert.pem).
# Usage : scripts/backup/backup.sh <dossier_de_sortie>
set -euo pipefail
: "${BACKUP_DATABASE_URL:?BACKUP_DATABASE_URL manquant}"
: "${BACKUP_CERT_PEM:?BACKUP_CERT_PEM manquant}"
out_dir="${1:?dossier de sortie requis}"
mkdir -p "$out_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
file="$out_dir/annour-$stamp.archive.gz.cms"
cert="$(mktemp)"
trap 'rm -f "$cert"' EXIT
printf '%s\n' "$BACKUP_CERT_PEM" > "$cert"

# La donnée en clair ne touche jamais le disque : flux mongodump -> chiffrement
mongodump --uri="$BACKUP_DATABASE_URL" --archive --gzip --quiet \
  | openssl cms -encrypt -binary -stream -aes-256-cbc -outform DER -out "$file" "$cert"

# Une archive vide ou minuscule signale un échec silencieux
size=$(wc -c < "$file")
[ "$size" -gt 1024 ] || { echo "Sauvegarde anormalement petite ($size octets)" >&2; exit 1; }
sha256sum "$file" > "$file.sha256"
echo "Sauvegarde chiffrée : $(basename "$file") ($size octets)"
