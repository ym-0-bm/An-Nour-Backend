"""
Règles métier sans base de données (tests de caractérisation).

Ils figent le comportement actuel. Les anomalies constatées sont marquées xfail(strict=True) :
le jour où elles sont corrigées, le test passe et pytest le signale pour qu'on retire le marqueur.
"""
import asyncio
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.utils import bulletin, matricule_generator, receipt_analyzer
from app.utils.bulletin import calculer_moyenne, get_mention
from app.utils.finance_utils import generate_reference


def note(value, type_="EVALUATION", minutes=0):
    return SimpleNamespace(note=value, type=type_, created_at=datetime(2026, 1, 1) + timedelta(minutes=minutes))


# ---------------------------------------------------------------------------
# Moyennes, mentions, rangs
# ---------------------------------------------------------------------------

def test_moyenne_sur_quatre_notes():
    notes = [note(12, "TEST_ENTREE"), note(14, minutes=1), note(16, minutes=2), note(18, minutes=3)]
    assert calculer_moyenne(notes) == 15.0


def test_moyenne_notes_manquantes_comptent_zero():
    assert calculer_moyenne([note(12, "TEST_ENTREE"), note(16, minutes=1)]) == 7.0
    assert calculer_moyenne([]) == 0


def test_moyenne_ne_garde_que_les_trois_premieres_evaluations():
    notes = [note(10, "TEST_ENTREE"), note(20, minutes=4), note(10, minutes=1), note(10, minutes=2), note(10, minutes=3)]
    assert calculer_moyenne(notes) == 10.0


def test_moyenne_ignore_les_autres_types():
    assert calculer_moyenne([note(20, "SUIVI"), note(8, "TEST_ENTREE")]) == 2.0


def test_moyenne_arrondie_a_deux_decimales():
    assert calculer_moyenne([note(13, "TEST_ENTREE"), note(13), note(13), note(14)]) == 13.25
    assert calculer_moyenne([note(10, "TEST_ENTREE"), note(10), note(10, minutes=1), note(10.01, minutes=2)]) == 10.0


@pytest.mark.parametrize("moyenne,mention", [
    (20, "Excellent"), (16, "Excellent"), (15.99, "Très bien"), (14, "Très bien"),
    (13.99, "Bien"), (12, "Bien"), (11.99, "Passable"), (10, "Passable"), (9.99, "Insuffisant"), (0, "Insuffisant"),
])
def test_mentions(moyenne, mention):
    assert get_mention(moyenne) == mention


def fake_notes_db(monkeypatch, notes_by_matricule, niveaux=None):
    async def registrations_find_many(**kwargs):
        return [SimpleNamespace(matricule=m) for m in notes_by_matricule]

    async def seminaristes_find_many(where):
        return [SimpleNamespace(matricule=m) for m, n in (niveaux or {}).items() if n == where["niveau"]]

    async def notes_find_many(where):
        return notes_by_matricule[where["matricule"]]

    monkeypatch.setattr(bulletin.prisma, "registration", SimpleNamespace(find_many=registrations_find_many))
    monkeypatch.setattr(bulletin.prisma, "seminariste", SimpleNamespace(find_many=seminaristes_find_many))
    monkeypatch.setattr(bulletin.prisma, "note", SimpleNamespace(find_many=notes_find_many))


def test_rangs_par_moyenne_decroissante(monkeypatch):
    fake_notes_db(monkeypatch, {
        "A": [note(8, "TEST_ENTREE")],
        "B": [note(20, "TEST_ENTREE")],
        "C": [note(12, "TEST_ENTREE")],
    })
    rangs, total = asyncio.run(bulletin.calculer_rangs())
    assert rangs == {"B": 1, "C": 2, "A": 3} and total == 3


def test_rangs_filtres_par_niveau(monkeypatch):
    fake_notes_db(monkeypatch, {"A": [note(8, "TEST_ENTREE")], "B": [note(20, "TEST_ENTREE")]},
                  niveaux={"A": "N1", "B": "N2"})
    assert asyncio.run(bulletin.calculer_rangs("N1")) == ({"A": 1}, 1)
    assert asyncio.run(bulletin.calculer_rangs("N3")) == ({}, 0)


def test_rangs_ex_aequo_comportement_actuel(monkeypatch):
    """Comportement ACTUEL : deux moyennes égales reçoivent des rangs différents (1 et 2).
    Règle métier à confirmer en Partie 7 (rang partagé 1, 1, 3 ?)."""
    fake_notes_db(monkeypatch, {"A": [note(12, "TEST_ENTREE")], "B": [note(12, "TEST_ENTREE")]})
    rangs, _ = asyncio.run(bulletin.calculer_rangs())
    assert sorted(rangs.values()) == [1, 2]


# ---------------------------------------------------------------------------
# Matricules et références
# ---------------------------------------------------------------------------

def test_matricule_format_et_collision(monkeypatch):
    existing = {"ANNOUR25-NASSR-003"}

    async def count(where):
        return 2

    async def find_unique(where):
        return SimpleNamespace() if where["matricule"] in existing else None

    monkeypatch.setattr(matricule_generator.prisma, "registration", SimpleNamespace(count=count, find_unique=find_unique))
    assert asyncio.run(matricule_generator.generate_matricule("NASSR")) == "ANNOUR25-NASSR-004"


@pytest.mark.xfail(strict=True, reason="Anomalie A4 : l'année '25' est codée en dur dans le matricule (édition 2026 ?)")
def test_matricule_annee_non_codee_en_dur(monkeypatch):
    async def count(where):
        return 0

    async def find_unique(where):
        return None

    monkeypatch.setattr(matricule_generator.prisma, "registration", SimpleNamespace(count=count, find_unique=find_unique))
    assert not asyncio.run(matricule_generator.generate_matricule("D1")).startswith("ANNOUR25-")


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

    monkeypatch.setattr(receipt_analyzer.prisma, "registration", SimpleNamespace(find_first=find_first))
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


@pytest.mark.xfail(strict=True, reason="Anomalie A1 : sans ID lisible, un ID aléatoire 'TANNOUR…' est généré et le reçu est validé")
def test_recu_sans_id_de_transaction_refuse(analyzer):
    assert analyze(analyzer, GOOD_RECEIPT.replace("TABCDEFGHJKLMNPQR", ""))["isValid"] is False


@pytest.mark.xfail(strict=True, reason="Anomalie A2 : `error = errors` compare une liste ; les messages ciblés ne sont jamais renvoyés")
def test_recu_non_wave_message_cible(analyzer):
    result = analyze(analyzer, GOOD_RECEIPT.replace("En partenariat avec UBA", ""))
    assert result["isValid"] is False
    assert result["message"] == "Ceci n'est pas un reçu Wave. Envoyez une capture depuis votre application Wave."


@pytest.mark.xfail(strict=True, reason="Anomalie A5 : les retours à la ligne sont supprimés, l'ID se colle à la ligne suivante")
def test_recu_id_suivi_d_une_ligne(analyzer):
    text = "-6.000F\nPaiement AN-NOUR\nStatut Effectué\nTABCDEFGHJKLMNPQR\nEn partenariat avec UBA"
    assert analyze(analyzer, text)["extractedData"]["transactionId"] == "TABCDEFGHJKLMNPQR"


@pytest.mark.xfail(strict=True, reason="Anomalie A3 : le texte OCR complet (données personnelles) est écrit sur la sortie standard")
def test_recu_texte_ocr_non_journalise(analyzer, capsys):
    analyze(analyzer, GOOD_RECEIPT)
    assert "PaiementAN" not in capsys.readouterr().out
