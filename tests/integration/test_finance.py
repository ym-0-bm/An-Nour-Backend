"""Module Finances (Partie 5, B1) : parcours complets API → MongoDB."""
from datetime import datetime, timedelta

import pytest

from app.config import settings
from tests.integration.test_flows import register_and_verify, registration_body

API = f"{settings.API_V1_STR}/finances"
BASE_DATE = datetime(2026, 3, 1, 10, 0, 0)


@pytest.fixture
def fin(seed_user, login):
    return login(seed_user("fin.integ", "finances"))


def day(n):
    return (BASE_DATE + timedelta(days=n)).isoformat()


def create_don(api, headers, montant, date=None, donateur="Donateur Fictif"):
    body = {"montant": montant, "donateur": donateur}
    if date:
        body["date_transaction"] = date
    response = api.post(f"{API}/entrees/don", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def create_vente(api, headers, montant, date=None):
    body = {"montant": montant, "libelle": "Vente fictive de tickets"}
    if date:
        body["date_transaction"] = date
    response = api.post(f"{API}/entrees/vente", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def create_sortie(api, headers, montant, date=None, categorie="Achat matériel", libelle="Achat fictif"):
    body = {"montant": montant, "categorie": categorie, "libelle": libelle, "beneficiaire": "Fournisseur Fictif"}
    if date:
        body["date_transaction"] = date
    response = api.post(f"{API}/sorties", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def delete_sortie(api, headers, reference, reason="Saisie en double"):
    return api.request("DELETE", f"{API}/sorties/{reference}", json={"deleted_reason": reason}, headers=headers)


# ---------------------------------------------------------------------------
# Entrées
# ---------------------------------------------------------------------------

def test_entrees_pagination_is_ordered_across_pages(api, fin):
    # Créées dans le désordre : l'ordre doit venir de la base, pas de la page
    refs = {n: create_don(api, fin, 1000 + n, day(n))["reference"] for n in (2, 4, 1, 3)}

    page1 = api.get(f"{API}/entrees", params={"page": 1, "limit": 2}, headers=fin).json()
    page2 = api.get(f"{API}/entrees", params={"page": 2, "limit": 2}, headers=fin).json()

    assert [t["reference"] for t in page1["data"]] == [refs[4], refs[3]]
    assert [t["reference"] for t in page2["data"]] == [refs[2], refs[1]]
    assert page1["total"] == 4
    assert page1["total_montant"] == pytest.approx(1001 + 1002 + 1003 + 1004)


def test_entrees_filter_and_totals(api, fin):
    create_don(api, fin, 1000)
    create_vente(api, fin, 500)

    dons = api.get(f"{API}/entrees", params={"categorie": "Don"}, headers=fin).json()
    assert dons["total"] == 1 and dons["total_montant"] == pytest.approx(1000)

    toutes = api.get(f"{API}/entrees", headers=fin).json()
    assert toutes["total"] == 2 and toutes["total_montant"] == pytest.approx(1500)
    assert {t["categorie"] for t in toutes["data"]} == {"Don", "Vente"}


def test_entree_detail_and_type_check(api, fin):
    reference = create_don(api, fin, 1000)["reference"]
    assert api.get(f"{API}/entrees/{reference}", headers=fin).json()["montant"] == 1000
    assert api.get(f"{API}/sorties/{reference}", headers=fin).status_code == 404
    assert api.get(f"{API}/entrees/INEXISTANTE", headers=fin).status_code == 404


def test_entree_names_seminariste_when_linked(api, fin, seed_dortoir):
    seed_dortoir()
    matricule = register_and_verify(api, fin)
    data = api.get(f"{API}/entrees", params={"categorie": "Inscription"}, headers=fin).json()["data"]
    assert data[0]["matricule"] == matricule
    assert data[0]["nom_seminariste"] == "FICTIF SEMINARISTE"


# ---------------------------------------------------------------------------
# Sorties : cycle de vie, corbeille, audit
# ---------------------------------------------------------------------------

def test_sortie_lifecycle_is_fully_audited(api, fin):
    reference = create_sortie(api, fin, 3000)["reference"]

    listed = api.get(f"{API}/sorties", headers=fin).json()
    assert listed["total"] == 1
    assert listed["total_sorties"] == pytest.approx(3000) and listed["solde_periode"] == pytest.approx(-3000)

    updated = api.put(f"{API}/sorties/{reference}", json={"montant": 3500, "libelle": "Achat corrigé"}, headers=fin)
    assert updated.status_code == 200 and updated.json()["montant"] == 3500

    assert delete_sortie(api, fin, reference).status_code == 200
    assert api.get(f"{API}/sorties", headers=fin).json()["total"] == 0
    trash = api.get(f"{API}/sorties-deleted", headers=fin).json()
    assert [t["reference"] for t in trash["data"]] == [reference]
    assert trash["data"][0]["deleted_reason"] == "Saisie en double"

    # Une sortie supprimée ne peut être ni modifiée ni supprimée à nouveau
    assert api.put(f"{API}/sorties/{reference}", json={"montant": 1}, headers=fin).status_code == 400
    assert delete_sortie(api, fin, reference).status_code == 400

    assert api.post(f"{API}/sorties/{reference}/restore", headers=fin).status_code == 200
    assert api.post(f"{API}/sorties/{reference}/restore", headers=fin).status_code == 400
    assert api.get(f"{API}/sorties", headers=fin).json()["total"] == 1

    audit = api.get(f"{API}/audit/{reference}", headers=fin).json()
    actions = [(a["action"], a.get("field_changed")) for a in audit]
    assert sorted(actions, key=str) == sorted(
        [("CREATE", None), ("UPDATE", "montant"), ("UPDATE", "libelle"), ("DELETE", None), ("RESTORE", None)], key=str)
    assert {a["modified_by"] for a in audit} == {"fin.integ"}
    montant_change = next(a for a in audit if a.get("field_changed") == "montant")
    assert (montant_change["old_value"], montant_change["new_value"]) == ("3000.0", "3500.0")


def test_sortie_requires_categorie(api, fin):
    response = api.post(f"{API}/sorties", json={"montant": 100, "libelle": "Sans catégorie"}, headers=fin)
    assert response.status_code == 422


@pytest.mark.parametrize("field", ["montant", "libelle", "categorie", "mode_paiement", "date_transaction"])
def test_sortie_update_rejects_null_for_required_fields(api, fin, field):
    reference = create_sortie(api, fin, 100)["reference"]
    assert api.put(f"{API}/sorties/{reference}", json={field: None}, headers=fin).status_code == 422


@pytest.mark.parametrize("montant", [0, -50])
def test_sortie_update_rejects_non_positive_amount(api, fin, montant):
    reference = create_sortie(api, fin, 100)["reference"]
    assert api.put(f"{API}/sorties/{reference}", json={"montant": montant}, headers=fin).status_code == 422


def test_sortie_update_can_clear_optional_beneficiaire(api, fin):
    reference = create_sortie(api, fin, 100)["reference"]
    response = api.put(f"{API}/sorties/{reference}", json={"beneficiaire": None}, headers=fin)
    assert response.status_code == 200 and response.json()["beneficiaire"] is None


def test_sorties_filters_by_date_and_search(api, fin):
    create_sortie(api, fin, 100, day(1), libelle="Achat de riz")
    create_sortie(api, fin, 200, day(5), libelle="Location de chaises")

    in_range = api.get(f"{API}/sorties", params={"date_debut": day(0), "date_fin": day(2)}, headers=fin).json()
    assert in_range["total"] == 1 and in_range["total_sorties"] == pytest.approx(100)

    found = api.get(f"{API}/sorties", params={"search": "chaises"}, headers=fin).json()
    assert [t["libelle"] for t in found["data"]] == ["Location de chaises"]


def test_sorties_pagination_is_ordered_across_pages(api, fin):
    refs = {n: create_sortie(api, fin, 10 * n, day(n))["reference"] for n in (3, 1, 2)}
    page1 = api.get(f"{API}/sorties", params={"page": 1, "limit": 2}, headers=fin).json()
    assert [t["reference"] for t in page1["data"]] == [refs[3], refs[2]]
    assert page1["total_sorties"] == pytest.approx(60)


# ---------------------------------------------------------------------------
# Tableau de bord et rapports
# ---------------------------------------------------------------------------

def test_dashboard_totals_exclude_deleted(api, fin, seed_dortoir):
    seed_dortoir()
    register_and_verify(api, fin)
    create_don(api, fin, 1000, day(1))
    create_vente(api, fin, 500, day(2))
    create_sortie(api, fin, 2000, day(3), categorie="Restauration")
    deleted = create_sortie(api, fin, 999, day(4))["reference"]
    assert delete_sortie(api, fin, deleted).status_code == 200

    dashboard = api.get(f"{API}/dashboard", headers=fin).json()
    fee = float(settings.REGISTRATION_FEE)
    assert dashboard["inscriptions"] == {"count": 1, "montant": fee}
    assert dashboard["dons"] == {"count": 1, "montant": 1000}
    assert dashboard["ventes"] == {"count": 1, "montant": 500}
    assert dashboard["autres_entrees"] == {"count": 0, "montant": 0}
    assert dashboard["sorties"] == {"count": 1, "montant": 2000}
    assert dashboard["total_entrees"] == pytest.approx(fee + 1500)
    assert dashboard["total_sorties"] == pytest.approx(2000)
    assert dashboard["solde"] == pytest.approx(fee + 1500 - 2000)
    assert dashboard["repartition_entrees"] == {"Inscription": fee, "Don": 1000, "Vente": 500}
    assert dashboard["repartition_sorties"] == {"Restauration": 2000}

    recent = dashboard["transactions_recentes"]
    assert deleted not in {t["reference"] for t in recent}
    dates = [t["date_transaction"] for t in recent]
    assert dates == sorted(dates, reverse=True)


def test_dashboard_recent_transactions_are_limited_to_ten(api, fin):
    for n in range(12):
        create_don(api, fin, 10, day(n))
    recent = api.get(f"{API}/dashboard", headers=fin).json()["transactions_recentes"]
    assert len(recent) == 10
    assert recent[0]["date_transaction"].startswith(day(11)[:10])


def test_empty_dashboard(api, fin):
    dashboard = api.get(f"{API}/dashboard", headers=fin).json()
    assert dashboard["total_entrees"] == 0 and dashboard["total_sorties"] == 0 and dashboard["solde"] == 0
    assert dashboard["transactions_recentes"] == []


def test_rapport_covers_only_its_period(api, fin):
    create_don(api, fin, 1000, day(1))
    create_sortie(api, fin, 300, day(2))
    create_don(api, fin, 5000, day(30))  # hors période

    created = api.post(f"{API}/rapports/generate", json={
        "titre": "Rapport fictif", "periode_debut": day(0), "periode_fin": day(10), "type_rapport": "mensuel",
    }, headers=fin)
    assert created.status_code == 201, created.text
    rapport = created.json()
    assert (rapport["total_entrees"], rapport["total_sorties"], rapport["solde"]) == (1000, 300, 700)
    assert rapport["nb_transactions"] == 2
    assert rapport["generated_by"] == "fin.integ"

    assert rapport["numero"] in {r["numero"] for r in api.get(f"{API}/rapports", headers=fin).json()}
    detail = api.get(f"{API}/rapports/{rapport['numero']}", headers=fin)
    assert detail.status_code == 200
    assert api.get(f"{API}/rapports/INEXISTANT", headers=fin).status_code == 404


# ---------------------------------------------------------------------------
# Synchronisation inscriptions → finances
# ---------------------------------------------------------------------------

def test_sync_inscriptions_is_idempotent(api, fin, seed_dortoir, mongo_db):
    seed_dortoir()
    matricule = register_and_verify(api, fin)
    # Une inscription au paiement non vérifié n'est jamais synchronisée
    pending = api.post(f"{settings.API_V1_STR}/registrations", json=registration_body("TX-INTEG-PENDING")).json()["matricule"]
    assert api.post(f"{API}/sync/inscription/{pending}", headers=fin).status_code == 409
    # Simule une inscription antérieure au module finance : pas d'écriture associée
    mongo_db.transactions.delete_many({"matricule": matricule})

    status = api.get(f"{API}/sync/status", headers=fin).json()
    assert (status["total_registrations"], status["synced_count"], status["not_synced_count"]) == (1, 0, 1)

    first = api.post(f"{API}/sync/inscriptions", headers=fin).json()
    assert (first["created"], first["skipped"]) == (1, 0)
    second = api.post(f"{API}/sync/inscriptions", headers=fin).json()
    assert (second["created"], second["skipped"]) == (0, 1)
    assert mongo_db.transactions.count_documents({"matricule": matricule}) == 1

    status = api.get(f"{API}/sync/status", headers=fin).json()
    assert status["sync_percentage"] == 100

    single = api.post(f"{API}/sync/inscription/{matricule}", headers=fin)
    assert single.status_code == 200
    assert mongo_db.transactions.count_documents({"matricule": matricule}) == 1
    assert api.post(f"{API}/sync/inscription/INEXISTANT", headers=fin).status_code == 404
