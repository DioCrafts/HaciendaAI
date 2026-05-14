"""Tests del JSON Schema del corpus.

Verifica que el schema:
1. Es un Draft 2020-12 válido.
2. Acepta todos los ficheros del corpus actual.
3. Rechaza variaciones inválidas conocidas.
4. El CLI `hacienda-ai schema PATH...` devuelve códigos de salida correctos.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from hacienda_ai.cli import main

SCHEMA_PATH = Path(__file__).parent.parent / "src" / "hacienda_ai" / "data" / "corpus.schema.json"
CORPUS_DIR = Path(__file__).parent.parent / "src" / "hacienda_ai" / "data" / "deductions"


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _validator() -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(_schema())


def _valid_deduction() -> dict[str, Any]:
    """Construye una deducción mínima válida."""
    return {
        "id": "test_validada",
        "name": "Test",
        "description": "Test description",
        "tax_year": 2025,
        "scope": "estatal",
        "region": None,
        "category": "deduccion",
        "requirements": [{"field": "expenses.amount", "operator": ">", "value": 0}],
        "calculation": {"type": "fixed_amount", "fixed_amount": 100.0},
        "limit": None,
        "taxable_base_limits": {},
        "incompatibilities": [],
        "required_documents": ["Doc"],
        "rent_web_boxes": [],
        "sources": [{"type": "ley", "title": "Test source"}],
        "effective_from": "2025-01-01",
        "effective_to": "2025-12-31",
        "last_reviewed_at": None,
        "risk_level": "bajo",
        "validation_status": "validada",
    }


def test_schema_is_a_valid_draft_2020_12_schema() -> None:
    jsonschema.Draft202012Validator.check_schema(_schema())


def test_schema_accepts_all_corpus_files() -> None:
    validator = _validator()
    corpus_files = sorted(CORPUS_DIR.glob("*.json"))
    assert corpus_files, "No corpus files found"
    for path in corpus_files:
        data = json.loads(path.read_text(encoding="utf-8"))
        errors = list(validator.iter_errors(data))
        assert not errors, f"{path.name} no valida: " + "; ".join(
            f"{'/'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in errors[:3]
        )


def test_schema_rejects_unknown_scope() -> None:
    payload = {"deductions": [{**_valid_deduction(), "scope": "intergaláctico"}]}
    errors = list(_validator().iter_errors(payload))
    assert errors


def test_schema_rejects_autonomic_without_region() -> None:
    payload = {"deductions": [{**_valid_deduction(), "scope": "autonomico", "region": None}]}
    errors = list(_validator().iter_errors(payload))
    assert errors


def test_schema_accepts_autonomic_with_region() -> None:
    payload = {"deductions": [{**_valid_deduction(), "scope": "autonomico", "region": "Madrid"}]}
    errors = list(_validator().iter_errors(payload))
    assert not errors


def test_schema_rejects_tiered_percentage_without_tiers() -> None:
    deduction = _valid_deduction()
    deduction["calculation"] = {"type": "tiered_percentage", "base_field": "x"}
    errors = list(_validator().iter_errors({"deductions": [deduction]}))
    assert errors


def test_schema_rejects_tiers_in_non_tiered_calculation() -> None:
    deduction = _valid_deduction()
    deduction["calculation"] = {
        "type": "fixed_amount",
        "fixed_amount": 100.0,
        "tiers": [{"up_to": 100, "percentage": 0.5}],
    }
    errors = list(_validator().iter_errors({"deductions": [deduction]}))
    assert errors


def test_schema_rejects_prorated_without_monthly_amount() -> None:
    deduction = _valid_deduction()
    deduction["calculation"] = {"type": "prorated_fixed_amount", "months_field": "family.x"}
    errors = list(_validator().iter_errors({"deductions": [deduction]}))
    assert errors


def test_schema_rejects_invalid_operator() -> None:
    deduction = _valid_deduction()
    deduction["requirements"] = [{"field": "x", "operator": "matches", "value": "y"}]
    errors = list(_validator().iter_errors({"deductions": [deduction]}))
    assert errors


def test_schema_rejects_unknown_taxable_base_limit_key() -> None:
    deduction = _valid_deduction()
    deduction["taxable_base_limits"] = {"max_percentage_of_unknown": 0.10}
    errors = list(_validator().iter_errors({"deductions": [deduction]}))
    assert errors


def test_schema_rejects_percentage_out_of_range() -> None:
    deduction = _valid_deduction()
    deduction["calculation"] = {
        "type": "tiered_percentage",
        "base_field": "x",
        "tiers": [{"up_to": 100, "percentage": 1.5}],
    }
    errors = list(_validator().iter_errors({"deductions": [deduction]}))
    assert errors


def test_schema_rejects_empty_sources() -> None:
    deduction = _valid_deduction()
    deduction["sources"] = []
    errors = list(_validator().iter_errors({"deductions": [deduction]}))
    assert errors


def test_schema_rejects_bad_date_format() -> None:
    deduction = _valid_deduction()
    deduction["effective_from"] = "01/01/2025"
    errors = list(_validator().iter_errors({"deductions": [deduction]}))
    assert errors


def test_corpus_files_are_immune_to_known_bad_mutations() -> None:
    """Round-trip de seguridad: alteramos cada fichero del corpus con
    un error claro y comprobamos que el schema lo detecta."""
    for path in sorted(CORPUS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data["deductions"]:
            continue
        broken = copy.deepcopy(data)
        broken["deductions"][0]["risk_level"] = "extremo"  # valor inválido
        errors = list(_validator().iter_errors(broken))
        assert errors, f"{path.name}: la mutación del risk_level debería fallar"


# ---------- CLI `hacienda-ai schema` ----------


def test_cli_schema_validates_corpus_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    corpus_files = sorted(CORPUS_DIR.glob("*.json"))
    exit_code = main(["schema", *[str(p) for p in corpus_files]])
    captured = capsys.readouterr()
    assert exit_code == 0
    for path in corpus_files:
        assert f"{path}: OK" in captured.out


def test_cli_schema_fails_on_invalid_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps({"deductions": [{**_valid_deduction(), "scope": "intergaláctico"}]}),
        encoding="utf-8",
    )
    exit_code = main(["schema", str(bad)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "intergal" in captured.err


def test_cli_schema_reports_json_decode_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "broken.json"
    bad.write_text("{ not json", encoding="utf-8")
    exit_code = main(["schema", str(bad)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "JSON inválido" in captured.err
