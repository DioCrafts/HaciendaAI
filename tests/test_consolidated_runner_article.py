"""Tests de integración del runner consolidado con `article_snapshots_dir`.

Verifica que cuando el caller inyecta `article_snapshots_dir`:

1. El primer chequeo persiste el timeline completo + reporta bootstrap.
2. Sin cambios entre dos ejecuciones, no hay article-version drift.
3. Si el XML cambia (reforma de un artículo), el reporte lo refleja con
   los kinds correctos (added/rewritten/shifted).
4. Si el caller NO inyecta `article_snapshots_dir`, el comportamiento
   legacy se mantiene: `outcome.article_version_drift is None`.
5. Snapshot por artículo corrupto: error reportado pero el diff a fecha
   sigue siendo válido.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from hacienda_ai.models import (
    Norma,
    NormaRegistry,
    NormaStatus,
    SourceKind,
    VersionNorma,
)
from hacienda_ai.rag.cache import JsonAuditLog
from hacienda_ai.rag.consolidated import (
    article_snapshot_path,
    load_article_snapshot,
    run_check_for_registry,
    serialize_report,
)

FIXTURES = Path(__file__).parent / "fixtures" / "boe"
LIRPF_XML = (FIXTURES / "consolidado_lirpf_mini.xml").read_text(encoding="utf-8")


class _FakeFetcher:
    def __init__(self, payloads: dict[str, str]) -> None:
        self.payloads = payloads
        self.invalidations: list[str] = []

    def fetch(self, boe_id: str) -> str:
        return self.payloads[boe_id]

    def invalidate(self, boe_id: str) -> None:
        self.invalidations.append(boe_id)


def _registry() -> NormaRegistry:
    r = NormaRegistry()
    r.register_norma(
        Norma(
            boe_id="BOE-A-2006-20764",
            kind=SourceKind.LEY,
            title="LIRPF (test)",
            enacted_at=date(2006, 11, 28),
        )
    )
    r.register_version(
        VersionNorma(
            norma_boe_id="BOE-A-2006-20764",
            effective_from=date(2007, 1, 1),
            status=NormaStatus.VIGENTE,
        )
    )
    return r


def _run(
    fetcher: _FakeFetcher,
    tmp_path: Path,
    *,
    article_snapshots_dir: Path | None = None,
    today: date = date(2024, 1, 1),
):
    log = JsonAuditLog(path=tmp_path / "rag_invalidations.json")
    return run_check_for_registry(
        _registry(),
        fetcher=fetcher,  # type: ignore[arg-type]
        snapshots_dir=tmp_path / "snapshots",
        rag_cache=log,
        reference_date=today,
        today=today,
        article_snapshots_dir=article_snapshots_dir,
    )


# ---------- Sin article_snapshots_dir: comportamiento legacy ----------


def test_runner_sin_article_snapshots_dir_no_calcula_article_drift(
    tmp_path: Path,
) -> None:
    fetcher = _FakeFetcher({"BOE-A-2006-20764": LIRPF_XML})
    report = _run(fetcher, tmp_path)  # sin article_snapshots_dir
    [outcome] = report.outcomes
    assert outcome.drift is not None
    assert outcome.article_version_drift is None


# ---------- Bootstrap por artículo ----------


def test_runner_bootstrap_article_persiste_timeline_completo(
    tmp_path: Path,
) -> None:
    fetcher = _FakeFetcher({"BOE-A-2006-20764": LIRPF_XML})
    art_dir = tmp_path / "article_snapshots"
    report = _run(fetcher, tmp_path, article_snapshots_dir=art_dir)

    [outcome] = report.outcomes
    assert outcome.article_version_drift is not None
    assert outcome.article_version_drift.is_bootstrap is True
    assert outcome.article_version_drift.has_changes is False

    # El snapshot por artículo debe persistirse desde la primera pasada.
    path = article_snapshot_path(art_dir, "BOE-A-2006-20764")
    assert path.exists()
    snap = load_article_snapshot(art_dir, "BOE-A-2006-20764")
    assert snap is not None
    # LIRPF mini: 7 versiones (a1=2, a2=1, a19=2, a81bis=1, dadecimoctava=1).
    assert snap.total_versions == 7
    assert snap.article_ids == {"a1", "a2", "a19", "a81bis", "dadecimoctava"}


# ---------- Segunda ejecución sin cambios ----------


def test_runner_sin_cambios_no_reporta_article_drift(tmp_path: Path) -> None:
    fetcher = _FakeFetcher({"BOE-A-2006-20764": LIRPF_XML})
    art_dir = tmp_path / "article_snapshots"
    # Bootstrap.
    _run(fetcher, tmp_path, article_snapshots_dir=art_dir)
    # Segunda pasada con el mismo XML.
    report = _run(fetcher, tmp_path, article_snapshots_dir=art_dir)
    [outcome] = report.outcomes
    assert outcome.article_version_drift is not None
    assert outcome.article_version_drift.is_bootstrap is False
    assert outcome.article_version_drift.has_changes is False


# ---------- Drift por reescritura de un artículo ----------


def test_runner_rewritten_cuando_cambia_texto_en_misma_fecha(
    tmp_path: Path,
) -> None:
    """Una corrección oficial cambia el texto del art. 19 (versión vigente
    desde 2015) sin cambiar las fechas. El runner debe reportar `rewritten`."""
    fetcher = _FakeFetcher({"BOE-A-2006-20764": LIRPF_XML})
    art_dir = tmp_path / "article_snapshots"
    _run(fetcher, tmp_path, article_snapshots_dir=art_dir)

    mutated = LIRPF_XML.replace(
        "Se consideraran como tales los siguientes gastos, siempre que esten debidamente justificados.",
        "Se consideraran como tales los siguientes gastos, siempre que esten debidamente acreditados.",
    )
    assert mutated != LIRPF_XML  # sanity: la mutación funcionó
    fetcher.payloads["BOE-A-2006-20764"] = mutated

    report = _run(fetcher, tmp_path, article_snapshots_dir=art_dir)
    [outcome] = report.outcomes
    assert outcome.article_version_drift is not None
    avd = outcome.article_version_drift
    assert avd.has_changes is True
    # El cambio es en el texto del a19 versión 2015 → `rewritten`.
    rewritten_ids = {(d.article_id, d.effective_from) for d in avd.rewritten}
    assert ("a19", date(2015, 1, 1)) in rewritten_ids


# ---------- Drift por nueva versión añadida ----------


_MINI_XML_V1 = """<?xml version="1.0"?>
<legislacion-consolidada>
<meta><identificador>BOE-A-2006-20764</identificador></meta>
<texto>
<bloque id="a23" tipo="precepto">
  <version fecha_vigencia="20070101">
    <p class="parrafo">Texto original del art. 23.</p>
  </version>
</bloque>
</texto>
</legislacion-consolidada>"""

_MINI_XML_V2 = """<?xml version="1.0"?>
<legislacion-consolidada>
<meta><identificador>BOE-A-2006-20764</identificador></meta>
<texto>
<bloque id="a23" tipo="precepto">
  <version fecha_vigencia="20070101" fecha_vigencia_fin="20241231">
    <p class="parrafo">Texto original del art. 23.</p>
  </version>
  <version fecha_vigencia="20250101">
    <p class="parrafo">Reforma 2025: nueva redaccion del art. 23.</p>
    <p class="nota_pie">Modificado por BOE-A-2024-99999.</p>
  </version>
</bloque>
</texto>
</legislacion-consolidada>"""


def test_runner_added_cuando_nueva_version_aparece(tmp_path: Path) -> None:
    """Una reforma introduce una nueva `<version>` con effective_from
    posterior. El runner debe reportar `added` (la nueva) y `shifted`
    (la anterior cambia su `effective_to` de None a la víspera).
    También resuelve `modified_by_boe_id` desde la nota al pie."""
    fetcher = _FakeFetcher({"BOE-A-2006-20764": _MINI_XML_V1})
    art_dir = tmp_path / "article_snapshots"
    _run(fetcher, tmp_path, article_snapshots_dir=art_dir)

    # Versión 2 del XML: reforma a23 con efecto desde 2025.
    fetcher.payloads["BOE-A-2006-20764"] = _MINI_XML_V2

    report = _run(
        fetcher,
        tmp_path,
        article_snapshots_dir=art_dir,
        today=date(2025, 6, 1),
    )
    [outcome] = report.outcomes
    avd = outcome.article_version_drift
    assert avd is not None
    assert avd.has_changes is True

    added_keys = {(d.article_id, d.effective_from) for d in avd.added}
    assert ("a23", date(2025, 1, 1)) in added_keys

    shifted_keys = {(d.article_id, d.effective_from) for d in avd.shifted}
    assert ("a23", date(2007, 1, 1)) in shifted_keys

    new_v = next(
        d.current for d in avd.added if d.effective_from == date(2025, 1, 1)
    )
    assert new_v is not None
    assert new_v.modified_by_boe_id == "BOE-A-2024-99999"


# ---------- Snapshot corrupto ----------


def test_runner_snapshot_articulo_corrupto_reporta_error_pero_drift_sigue(
    tmp_path: Path,
) -> None:
    """Si el snapshot por artículo está corrupto, el outcome lleva error
    pero `drift` (a fecha) sigue siendo válido — el diff a fecha NO
    depende del snapshot por artículo."""
    fetcher = _FakeFetcher({"BOE-A-2006-20764": LIRPF_XML})
    art_dir = tmp_path / "article_snapshots"
    art_dir.mkdir(parents=True)
    (art_dir / "BOE-A-2006-20764.json").write_text(
        "{ corrupted json", encoding="utf-8"
    )

    report = _run(fetcher, tmp_path, article_snapshots_dir=art_dir)
    [outcome] = report.outcomes
    assert outcome.error is not None
    assert "article snapshot corrupto" in outcome.error
    # El diff a fecha sí se calculó (bootstrap NormaSnapshot).
    assert outcome.drift is not None
    assert outcome.drift.is_bootstrap is True
    # El diff por artículo NO (porque snapshot previo estaba corrupto).
    assert outcome.article_version_drift is None


# ---------- serialize_report incluye article_version_drift ----------


def test_serialize_report_incluye_article_version_drift(
    tmp_path: Path,
) -> None:
    fetcher = _FakeFetcher({"BOE-A-2006-20764": LIRPF_XML})
    art_dir = tmp_path / "article_snapshots"
    report = _run(fetcher, tmp_path, article_snapshots_dir=art_dir)
    serialized = serialize_report(report)
    payload = json.loads(
        json.dumps(serialized)
    )  # asegura serializable a JSON estándar
    [outcome] = payload["outcomes"]
    assert "article_version_drift" in outcome
    assert outcome["article_version_drift"]["is_bootstrap"] is True


def test_serialize_report_omite_article_version_drift_si_none(
    tmp_path: Path,
) -> None:
    fetcher = _FakeFetcher({"BOE-A-2006-20764": LIRPF_XML})
    report = _run(fetcher, tmp_path)  # sin article_snapshots_dir
    serialized = serialize_report(report)
    [outcome] = serialized["outcomes"]  # type: ignore[index]
    assert "article_version_drift" not in outcome  # type: ignore[operator]
