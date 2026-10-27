# Sauvegarde et restauration de la base MongoDB (B15)

## Principe

| Élément | Où | Rôle |
|---|---|---|
| Workflow `.github/workflows/backup.yml` | GitHub Actions | `mongodump` quotidien (02:30 UTC) ou à la demande |
| `scripts/backup/backup.sh` | runner | Dump compressé **chiffré en flux** (aucune donnée en clair sur disque) |
| Certificat public `BACKUP_CERT_PEM` | secret GitHub | Sert uniquement à **chiffrer** |
| Clé privée `backup_private_key.pem` | **hors ligne** (coffre, gestionnaire de mots de passe) | Seule capable de **déchiffrer** |
| Archive `annour-AAAAMMJJTHHMMSSZ.archive.gz.cms` + `.sha256` | artefact GitHub, 30 jours | Sauvegarde chiffrée (CMS, AES-256-CBC, clé RSA 4096) |

Une fuite de l'artefact ou du secret GitHub ne permet pas de lire les données : il faut la clé privée.

## Mise en place (une seule fois)

1. Sur un poste de confiance : `bash scripts/backup/generate_keys.sh <dossier>`.
2. Copier le contenu de `backup_public_cert.pem` dans le secret GitHub **`BACKUP_CERT_PEM`**.
3. Mettre `backup_private_key.pem` **hors ligne** puis l'effacer du poste. Ne jamais la committer.
4. Dans Atlas, créer un utilisateur **en lecture seule** (`read` sur la base) et placer son URI dans le
   secret **`BACKUP_DATABASE_URL`**.
5. **Accès réseau des runners GitHub (sans ouvrir 0.0.0.0/0)** : les IP des runners changent à chaque exécution.
   Le workflow ajoute donc l'IP du runner à la liste d'accès Atlas **pour 1 heure au plus** (`deleteAfterDate`),
   attend qu'elle soit active, fait la sauvegarde, puis **la retire**, même en cas d'échec.
   - Atlas → *Project Settings* → *Access Manager* → *Applications* → *API Keys* : créer une clé de projet avec le
     **seul** rôle **Project Network Access Manager**.
   - Secrets GitHub : `ATLAS_PUBLIC_KEY`, `ATLAS_PRIVATE_KEY`, `ATLAS_PROJECT_ID` (*Project Settings* → *Project ID*).
   - Si l'organisation exige une liste d'accès pour l'API d'administration, cette exigence s'applique aussi
     à la clé : la désactiver pour cette clé ou passer par un runner à IP fixe.
6. Lancer le workflow à la main (*Actions → Sauvegarde MongoDB → Run workflow*) et vérifier l'artefact.

## Restauration

Toujours restaurer **d'abord dans une base de vérification**, jamais directement sur la production :

```bash
# 1. Télécharger l'artefact (archive .cms et .sha256) depuis GitHub Actions
# 2. Restaurer dans une autre base (l'empreinte SHA-256 est vérifiée avant tout déchiffrement)
bash scripts/backup/restore.sh annour-XXXX.archive.gz.cms backup_private_key.pem \
     "<URI du cluster>" annour_db annour_restore_test
```

Vérifier ensuite le nombre de documents par collection et quelques documents témoins.

**Reprise après sinistre** (remplacement des collections de production) : ajouter `--drop` et cibler
`annour_db`, **uniquement** après accord explicite, en sauvegardant l'état courant juste avant.

## Contrôles effectués (base de test)

- Archive : enveloppe CMS chiffrée AES-256-CBC ; illisible sans la clé privée.
- Restauration complète dans une autre base : nombres de documents, documents et index uniques identiques ;
  base source intacte.
- Archive altérée : refusée par la vérification SHA-256, rien n'est restauré.
- Mauvaise clé privée : déchiffrement refusé, rien n'est restauré.

## Limites

- Les artefacts GitHub sont conservés 30 jours : au-delà, archiver hors de GitHub si une rétention plus
  longue est requise par la politique de conservation des données.
- La sauvegarde n'est pas transactionnelle entre collections (`mongodump` sans `--oplog` sur Atlas partagé).
