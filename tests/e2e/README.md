# Tests de bout en bout (Partie 11 / T1)

Six parcours joués **uniquement par HTTP** contre l'API dans son **image Docker de production**
(utilisateur non-root, Tesseract réel), comme le font la PWA et l'interface d'administration :

| Parcours | Ce qui est vérifié |
|---|---|
| Inscription → reçu → OCR | Reçu lu par le vrai OCR, paiement « vérifié », entrée financière au tarif serveur ; reçu réutilisé refusé |
| Reçu illisible → validation manuelle | Statut erreur/rejet, puis validation par le rôle finances avec motif et historique |
| Notes → bulletins | Ex æquo (1, 1, 3) mis à jour sur tous les bulletins, recalcul après correction, refus si notes incomplètes |
| Santé | Fiche, consultation, export tracé, statistiques ; refus pour un autre rôle |
| Administration | Création de compte, révocation des sessions (ancien token refusé), export Excel |
| Sécurité | /health, en-têtes de sécurité, 401 sans token ou avec un faux token |

Garde-fou : API et base **locales**, nom de base contenant `test`, sinon arrêt avant toute écriture.
La base est vidée au début de chaque session.

## Lancer en local (Docker)

```bash
docker build -t annour-backend:e2e .
docker network create annour-e2e
docker run -d --name mongo-e2e --network annour-e2e -p 127.0.0.1:27118:27017 mongo:8.0 --replSet rs0 --bind_ip_all
docker exec mongo-e2e mongosh --quiet --eval 'rs.initiate({_id:"rs0", members:[{_id:0, host:"mongo-e2e:27017"}]})'
DATABASE_URL="mongodb://127.0.0.1:27118/annour_e2e_test?replicaSet=rs0&directConnection=true" \
    python -m prisma db push --skip-generate
docker run -d --name api-e2e --network annour-e2e -p 127.0.0.1:8000:8000 \
    -e DATABASE_URL="mongodb://mongo-e2e:27017/annour_e2e_test?replicaSet=rs0&directConnection=true" \
    -e ENVIRONMENT=test -e SECRET_KEY="e2e-only-secret-key-0123456789abcdefghij" \
    -e CLOUDINARY_CLOUD_NAME=t -e CLOUDINARY_API_KEY=t -e CLOUDINARY_API_SECRET=t annour-backend:e2e

E2E_BASE_URL=http://127.0.0.1:8000 \
E2E_DATABASE_URL="mongodb://127.0.0.1:27118/annour_e2e_test?replicaSet=rs0&directConnection=true" \
    pytest -q tests/e2e --no-cov

docker rm -f api-e2e mongo-e2e && docker network rm annour-e2e
```

Dans le CI, ces parcours s'exécutent dans le job d'intégration, après le test de fumée de l'image.
