#!/usr/bin/env bash
# Génère la paire de clés des sauvegardes (B15). À lancer UNE FOIS, sur un poste de confiance.
#   - backup_public_cert.pem  : à copier dans le secret GitHub BACKUP_CERT_PEM (sert à chiffrer)
#   - backup_private_key.pem  : à conserver HORS LIGNE (gestionnaire de mots de passe, coffre) ;
#                               seule clé capable de déchiffrer. Ne jamais la committer ni la mettre sur GitHub.
set -euo pipefail
# Git Bash (Windows) convertirait "/CN=..." en chemin de fichier : on exclut seulement cet argument
# de la conversion (les chemins de fichiers restent convertis). Sans effet sous Linux/macOS.
export MSYS2_ARG_CONV_EXCL="/CN"
out_dir="${1:-.}"
openssl req -x509 -newkey rsa:4096 -nodes -sha256 -days 3650 \
  -subj "/CN=An-Nour sauvegardes" \
  -keyout "$out_dir/backup_private_key.pem" -out "$out_dir/backup_public_cert.pem"
chmod 600 "$out_dir/backup_private_key.pem"
echo "Clés créées dans $out_dir (clé privée : à mettre hors ligne immédiatement)."
