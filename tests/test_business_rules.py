"""
Règles métier sans base de données (tests de caractérisation).

Les anomalies A1 à A5 relevées en Partie 4 sont corrigées (Parties 7 et 8) : leurs tests,
autrefois marqués xfail(strict=True), vérifient désormais le comportement attendu.
"""
import asyncio
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.utils import bulletin, matricule_generator, receipt_analyzer
from app.utils.bulletin import analyser_notes, calculer_moyenne, classer, get_mention
from app.utils.finance_utils import generate_reference


def note(value, type_="EVALUATION", minutes=0):
    return SimpleNamespace(note=value, type=type_, created_at=datetime(2026, 1, 1) + timedelta(minutes=minutes))


# ---------------------------------------------------------------------------
# Moyennes, mentions, rangs
# ---------------------------------------------------------------------------

def complete(entree, e1, e2, e3, offset=0):
    """Les 4 notes d'un séminariste (test d'entrée + 3 évaluations)."""
    return [note(entree, "TEST_ENTREE", offset), note(e1, minutes=offset + 1),
            note(e2, minutes=offset + 2), note(e3, minutes=offset + 3)]


def test_moyenne_sur_quatre_notes():
    assert calculer_moyenne(complete(12, 14, 16, 18)) == 15.0


@pytest.mark.parametrize("notes,manquantes", [
    ([], ["test_entree", "evaluation_1", "evaluation_2", "evaluation_3"]),
    ([note(12, "TEST_ENTREE"), note(16, minutes=1)], ["evaluation_2", "evaluation_3"]),
    ([note(10), note(10, minutes=1), note(10, minutes=2)], ["test_entree"]),
    ([note(20, "SUIVI"), note(8, "TEST_ENTREE")], ["evaluation_1", "evaluation_2", "evaluation_3"]),
])
def test_note_manquante_jamais_comptee_zero(notes, manquantes):
    analyse = analyser_notes(notes)
    assert analyse["moyenne"] is None and calculer_moyenne(notes) is None
    assert analyse["statut"] == "incomplet"
    assert analyse["notes_manquantes"] == manquantes


@pytest.mark.parametrize("valeur", [-1, 20.5, None])
def test_note_invalide_bloque_la_moyenne(valeur):
    analyse = analyser_notes(complete(12, 14, 16, 18)[:3] + [note(valeur, minutes=3)])
    assert analyse["moyenne"] is None
    assert analyse["statut"] == "invalide" and analyse["notes_invalides"] == ["evaluation_3"]


def test_moyenne_ne_garde_que_les_trois_premieres_evaluations():
    notes = [note(10, "TEST_ENTREE"), note(20, minutes=4), note(10, minutes=1), note(10, minutes=2), note(10, minutes=3)]
    assert calculer_moyenne(notes) == 10.0


def test_moyenne_arrondie_a_deux_decimales_sans_coefficient():
    assert calculer_moyenne(complete(13, 13, 13, 14)) == 13.25
    assert calculer_moyenne(complete(10, 10, 10, 10.01)) == 10.0
    assert calculer_moyenne(complete(0, 0, 0, 20)) == 5.0


@pytest.mark.parametrize("moyenne,mention", [
    (20, "Excellent"), (16, "Excellent"), (15.99, "Très bien"), (14, "Très bien"),
    (13.99, "Bien"), (12, "Bien"), (11.99, "Passable"), (10, "Passable"), (9.99, "Insuffisant"), (0, "Insuffisant"),
    (None, None),
])
def test_mentions(moyenne, mention):
    assert get_mention(moyenne) == mention


def test_classement_ex_aequo_specification():
    # Exemple de la spécification B7 : 18, 18, 16, 16, 14 -> 1, 1, 3, 3, 5
    assert classer({"A": 18, "B": 18, "C": 16, "D": 16, "E": 14}) == {"A": 1, "B": 1, "C": 3, "D": 3, "E": 5}
    assert classer({"A": 12, "B": 15, "C": 15}) == {"B": 1, "C": 1, "A": 3}
    assert classer({}) == {}


def fake_notes_db(monkeypatch, notes_by_matricule, niveaux=None):
    calls = []

    async def registrations_find_many(**kwargs):
        return [SimpleNamespace(matricule=m) for m in notes_by_matricule]

    async def seminaristes_find_many(where):
        return [SimpleNamespace(matricule=m) for m, n in (niveaux or {}).items() if n == where["niveau"]]

    async def notes_find_many(where):
        calls.append(where)
        wanted = where["matricule"]["in"]
        return [SimpleNamespace(matricule=m, **vars(n)) for m in wanted for n in notes_by_matricule.get(m, [])]

    monkeypatch.setattr(bulletin.prisma, "registration", SimpleNamespace(find_many=registrations_find_many))
    monkeypatch.setattr(bulletin.prisma, "seminariste", SimpleNamespace(find_many=seminaristes_find_many))
    monkeypatch.setattr(bulletin.prisma, "note", SimpleNamespace(find_many=notes_find_many))
    return calls


def test_rangs_ex_aequo_et_incomplets_non_classes(monkeypatch):
    calls = fake_notes_db(monkeypatch, {
        "A": complete(18, 18, 18, 18),
        "B": complete(18, 18, 18, 18),
        "C": complete(16, 16, 16, 16),
        "D": [note(20, "TEST_ENTREE")],  # incomplet : non classé
    })
    rangs, effectif = asyncio.run(bulletin.calculer_rangs())
    assert rangs == {"A": 1, "B": 1, "C": 3}
    assert effectif == 3
    assert len(calls) == 1  # toutes les notes en une seule requête


def test_rangs_filtres_par_niveau(monkeypatch):
    fake_notes_db(monkeypatch, {"A": complete(8, 8, 8, 8), "B": complete(20, 20, 20, 20)},
                  niveaux={"A": "N1", "B": "N2"})
    assert asyncio.run(bulletin.calculer_rangs("N1")) == ({"A": 1}, 1)
    assert asyncio.run(bulletin.calculer_rangs("N3")) == ({}, 0)


# ---------------------------------------------------------------------------
# Matricules (B8)
# ---------------------------------------------------------------------------

def fake_matricule_db(monkeypatch, existing=()):
    counters, existing = {}, set(existing)

    async def counter_find_unique(where):
        return SimpleNamespace(value=counters[where["key"]]) if where["key"] in counters else None

    async def counter_create(data):
        counters[data["key"]] = data["value"]

    async def counter_update(where, data):
        counters[where["key"]] += data["value"]["increment"]
        return SimpleNamespace(value=counters[where["key"]])

    async def reg_find_unique(where):
        return SimpleNamespace() if where["matricule"] in existing else None

    async def reg_find_many(where):
        prefix = where["matricule"]["startswith"]
        return [SimpleNamespace(matricule=m) for m in existing if m.startswith(prefix)]

    monkeypatch.setattr(matricule_generator.prisma, "matriculecounter", SimpleNamespace(
        find_unique=counter_find_unique, create=counter_create, update=counter_update))
    monkeypatch.setattr(matricule_generator.prisma, "registration", SimpleNamespace(
        find_unique=reg_find_unique, find_many=reg_find_many))
    return counters


@pytest.mark.parametrize("year,prefix", [(2026, "ANNOUR26"), (2027, "ANNOUR27"), (2099, "ANNOUR99")])
def test_prefixe_selon_annee_courante(monkeypatch, year, prefix):
    monkeypatch.setattr(matricule_generator.settings, "MATRICULE_YEAR", None)
    assert matricule_generator.matricule_prefix(datetime(year, 6, 1)) == prefix


def test_prefixe_force_par_configuration(monkeypatch):
    monkeypatch.setattr(matricule_generator.settings, "MATRICULE_YEAR", 2027)
    assert matricule_generator.matricule_prefix(datetime(2026, 12, 31)) == "ANNOUR27"


def test_matricule_reprend_apres_les_existants(monkeypatch):
    monkeypatch.setattr(matricule_generator.settings, "MATRICULE_YEAR", 2026)
    fake_matricule_db(monkeypatch, existing={"ANNOUR26-NASSR-001", "ANNOUR26-NASSR-007", "ANNOUR25-NASSR-050"})
    assert asyncio.run(matricule_generator.generate_matricule("NASSR")) == "ANNOUR26-NASSR-008"
    assert asyncio.run(matricule_generator.generate_matricule("NASSR")) == "ANNOUR26-NASSR-009"
    assert asyncio.run(matricule_generator.generate_matricule("AUTRE")) == "ANNOUR26-AUTRE-001"


def test_matricule_saute_un_numero_deja_pris(monkeypatch):
    monkeypatch.setattr(matricule_generator.settings, "MATRICULE_YEAR", 2026)
    counters = fake_matricule_db(monkeypatch, existing={"ANNOUR26-D1-002"})
    counters["ANNOUR26-D1"] = 1  # compteur en retard sur un matricule créé hors compteur
    assert asyncio.run(matricule_generator.generate_matricule("D1")) == "ANNOUR26-D1-003"


def test_reference_format_et_unicite():
    refs = {generate_reference("ENTREE") for _ in range(200)}
    assert len(refs) == 200
    assert all(re.fullmatch(r"ENT-\d{14}-[0-9A-F-]{8}", r) for r in refs)
    assert generate_reference("SORTIE").startswith("SOR-")


# ---------------------------------------------------------------------------
# Analyse des reçus Wave
# ---------------------------------------------------------------------------

# L'ID est en dernière ligne : voir l'anomalie A5 quand une ligne le suit
GOOD_RECEIPT = (
    "-6.000F\nPaiement AN-NOUR\nStatut Effectué\nEn partenariat avec UBA\nID de transaction\nTABCDEFGHJKLMNPQR"
)


@pytest.fixture
def analyzer(monkeypatch):
    used = set()

    async def find_first(where):
        return SimpleNamespace() if where["transaction_id"] in used else None

    async def used_find_unique(where):
        return SimpleNamespace() if where["transaction_id"] in used else None

    monkeypatch.setattr(receipt_analyzer.prisma, "registration", SimpleNamespace(find_first=find_first))
    monkeypatch.setattr(receipt_analyzer.prisma, "usedtransaction", SimpleNamespace(find_unique=used_find_unique))
    instance = receipt_analyzer.ReceiptAnalyzer()
    instance.used_ids = used
    return instance


def analyze(analyzer, text, expected=6000):
    return asyncio.run(analyzer.analyze_receipt(text, expected))


def test_recu_valide(analyzer):
    result = analyze(analyzer, GOOD_RECEIPT)
    assert result["isValid"] is True and result["score"] == 100
    assert result["extractedData"]["amount"] == 6000.0
    assert result["extractedData"]["transactionId"] == "TABCDEFGHJKLMNPQR"
    assert result["extractedData"]["accountName"] == "AN-NOUR"


def test_recu_mauvais_montant(analyzer):
    result = analyze(analyzer, GOOD_RECEIPT.replace("-6.000F", "-5.000F"))
    assert result["isValid"] is False
    assert result["message"] == "Montant invalide : vous avez payé 5000F, mais 6000F sont requis."


def test_recu_mauvais_destinataire(analyzer):
    result = analyze(analyzer, GOOD_RECEIPT.replace("AN-NOUR", "AUTRE COMPTE"))
    assert result["isValid"] is False
    assert "Destinataire incorrect" in result["message"]


def test_recu_transaction_deja_utilisee(analyzer):
    analyzer.used_ids.add("TABCDEFGHJKLMNPQR")
    result = analyze(analyzer, GOOD_RECEIPT)
    assert result["isValid"] is False
    assert result["message"] == "Cette transaction a déjà été utilisée pour une autre inscription"


def test_recu_illisible(analyzer):
    result = analyze(analyzer, "")
    assert result["isValid"] is False
    assert result["extractedData"]["amount"] is None


@pytest.mark.parametrize("raw,expected", [
    ("6.000", 6000.0), ("6 000", 6000.0), ("6,000.50", 6000.5), ("6,5", 6.5), ("-6.000", 6000.0), ("abc", None),
])
def test_normalisation_montants(analyzer, raw, expected):
    assert analyzer.normalize_amount(raw) == expected


def test_recu_sans_id_de_transaction_refuse(analyzer):
    assert analyze(analyzer, GOOD_RECEIPT.replace("TABCDEFGHJKLMNPQR", ""))["isValid"] is False


def test_recu_non_wave_message_cible(analyzer):
    result = analyze(analyzer, GOOD_RECEIPT.replace("En partenariat avec UBA", ""))
    assert result["isValid"] is False
    assert result["message"] == "Ceci n'est pas un reçu Wave. Envoyez une capture depuis votre application Wave."


def test_recu_id_suivi_d_une_ligne(analyzer):
    text = "-6.000F\nPaiement AN-NOUR\nStatut Effectué\nTABCDEFGHJKLMNPQR\nEn partenariat avec UBA"
    assert analyze(analyzer, text)["extractedData"]["transactionId"] == "TABCDEFGHJKLMNPQR"


def test_recu_texte_ocr_non_journalise(analyzer, capsys):
    analyze(analyzer, GOOD_RECEIPT)
    assert "PaiementAN" not in capsys.readouterr().out
