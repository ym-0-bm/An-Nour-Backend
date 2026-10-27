#!/usr/bin/env bash
# Restauration d'une sauvegarde chiffrée (B15).
# Usage : scripts/backup/restore.sh <sauvegarde.cms> <clé_privée.pem> <uri_cible> <base_source> <base_cible> [--drop]
#   Par défaut, restaure dans une AUTRE base (ex. annour_restore_test) pour vérification, sans rien écraser.
#   --drop remplace les collections existantes de la base cible : réservé à une reprise après sinistre,
#   après accord explicite et sauvegarde de l'état courant.
set -euo pipefail
archive="${1:?sauvegarde .cms requise}"; key="${2:?clé privée requise}"; target_uri="${3:?URI cible requise}"
source_db="${4:?base source requise}"; target_db="${5:?base cible requise}"; drop="${6:-}"

if [ -f "$archive.sha256" ]; then
  (cd "$(dirname "$archive")" && sha256sum -c "$(basename "$archive").sha256" >/dev/null) \
    || { echo "Empreinte SHA-256 invalide : archive altérée" >&2; exit 1; }
fi
args=(--uri="$target_uri" --archive --gzip --nsFrom="$source_db.*" --nsTo="$target_db.*")
if [ "$drop" = "--drop" ]; then
  echo "ATTENTION : les collections de $target_db vont être remplacées." >&2
  args+=(--drop)
fi
openssl cms -decrypt -binary -inform DER -in "$archive" -inkey "$key" | mongorestore "${args[@]}"
echo "Restauration terminée dans $target_db"
