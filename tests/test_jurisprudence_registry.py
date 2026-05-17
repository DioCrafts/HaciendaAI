"""Tests del `JurisprudenceRegistry`: índice + jerarquía + peso doctrinal."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from hacienda_ai.models import (
    ConsultaDGT,
    CriterioConfidence,
    FalloSentido,
    Impuesto,
    Organo,
    OrganoTEA,
    RatioConfidence,
    ResolucionTEAC,
    Sentencia,
    SentidoResolucion,
    TipoResolucion,
)
from hacienda_ai.safety import (
    DoctrineWeight,
    JurisprudenceRegistry,
    JurisprudenceTier,
)


def _make_sentencia(
    *,
    ecli: str,
    organo: Organo = Organo.TS,
    tribunal_codigo: str = "TS",
    sala: str = "Tercera",
    fallo_sentido: FalloSentido = FalloSentido.DESESTIMATORIA,
    resumen: str = "Asunto IRPF dietas trabajador desplazado",
    fecha: date = date(2024, 6, 15),
) -> Sentencia:
    return Sentencia(
        ecli=ecli,
        organo=organo,
        tribunal_codigo=tribunal_codigo,
        sala=sala,
        seccion=None,
        fecha=fecha,
        ponente=None,
        numero_resolucion=None,
        numero_recurso=None,
        fallo_sentido=fallo_sentido,
        fallo_texto="Desestimamos.",
        ratio_decidendi=None,
        ratio_confidence=RatioConfidence.AUTO,
        resumen=resumen,
        url=None,
        content_hash="a" * 64,
        last_fetched_at=date(2024, 9, 1),
    )


def _make_dgt(numero: str = "V0123-24") -> ConsultaDGT:
    return ConsultaDGT(
        numero=numero,
        fecha_salida=date(2024, 1, 30),
        fecha_entrada=None,
        impuesto=Impuesto.IRPF,
        asunto="Dietas IRPF",
        cuestion_planteada="...",
        contestacion_completa="...",
        criterio=None,
        criterio_confidence=CriterioConfidence.AUTO,
        normativa=("Ley 35/2006",),
        url=None,
        content_hash="b" * 64,
        last_fetched_at=date(2024, 9, 1),
    )


def _make_teac(
    *,
    numero: str = "00/12345/2023",
    organo: OrganoTEA = OrganoTEA.TEAC,
    tipo: TipoResolucion = TipoResolucion.UNIFICA_CRITERIO,
    sentido: SentidoResolucion = SentidoResolucion.DESESTIMATORIA,
    asunto: str = "Carga prueba dietas IRPF",
) -> ResolucionTEAC:
    return ResolucionTEAC(
        numero=numero,
        organo=organo,
        sede="Madrid",
        fecha=date(2023, 6, 15),
        tipo=tipo,
        sentido=sentido,
        impuesto=Impuesto.IRPF,
        asunto=asunto,
        criterio=None,
        criterio_confidence=CriterioConfidence.AUTO,
        normativa=("Ley 35/2006",),
        resolucion_texto="...",
        url=None,
        content_hash="c" * 64,
        last_fetched_at=date(2024, 9, 1),
    )


# ---------- Lookups ----------


def test_knows_ecli_exact_match() -> None:
    reg = JurisprudenceRegistry.from_items(
        sentencias=[_make_sentencia(ecli="ECLI:ES:TS:2024:1234")]
    )
    assert reg.knows_ecli("ECLI:ES:TS:2024:1234")


def test_knows_ecli_case_insensitive() -> None:
    """El LLM puede escribir el ECLI con cualquier capitalización; el
    lookup debe ser robusto."""
    reg = JurisprudenceRegistry.from_items(
        sentencias=[_make_sentencia(ecli="ECLI:ES:TS:2024:1234")]
    )
    assert reg.knows_ecli("ecli:es:ts:2024:1234")
    assert reg.knows_ecli("  ECLI:ES:TS:2024:1234  ")


def test_knows_ecli_missing_returns_false() -> None:
    reg = JurisprudenceRegistry.from_items(
        sentencias=[_make_sentencia(ecli="ECLI:ES:TS:2024:1234")]
    )
    assert not reg.knows_ecli("ECLI:ES:TS:2099:9999")


def test_knows_dgt_two_and_four_digit_year() -> None:
    """`V0123-24` y `V0123-2024` deben resolverse al mismo identificador."""
    reg = JurisprudenceRegistry.from_items(dgt_consultas=[_make_dgt("V0123-24")])
    assert reg.knows_dgt("V0123-24")
    assert reg.knows_dgt("V0123-2024")
    assert reg.knows_dgt("V123-24")  # padding inconsistente


def test_knows_teac_canonical_and_rg() -> None:
    """`R.G. 12345/2023` debe encontrar `00/12345/2023` (forma canónica)."""
    reg = JurisprudenceRegistry.from_items(teac_resoluciones=[_make_teac()])
    assert reg.knows_teac("00/12345/2023")
    assert reg.knows_teac("R.G. 12345/2023")
    assert reg.knows_teac("RG/12345/2023")
    assert reg.knows_teac("12345/2023")


def test_get_returns_metadata() -> None:
    reg = JurisprudenceRegistry.from_items(
        sentencias=[_make_sentencia(ecli="ECLI:ES:TS:2024:1234")]
    )
    entry = reg.get_sentencia("ECLI:ES:TS:2024:1234")
    assert entry is not None
    assert entry.organo == Organo.TS


# ---------- Tier ----------


def test_tier_for_ts_is_ts() -> None:
    reg = JurisprudenceRegistry.from_items(
        sentencias=[_make_sentencia(ecli="ECLI:ES:TS:2024:1234")]
    )
    assert reg.get_sentencia("ECLI:ES:TS:2024:1234").tier == JurisprudenceTier.TS


def test_tier_for_tc_is_tc() -> None:
    reg = JurisprudenceRegistry.from_items(
        sentencias=[
            _make_sentencia(
                ecli="ECLI:ES:TC:2021:182",
                organo=Organo.TC,
                tribunal_codigo="TC",
            )
        ]
    )
    assert (
        reg.get_sentencia("ECLI:ES:TC:2021:182").tier == JurisprudenceTier.TC
    )


def test_tier_for_teac_unifica_is_teac_unifica() -> None:
    reg = JurisprudenceRegistry.from_items(
        teac_resoluciones=[
            _make_teac(numero="00/12345/2023", tipo=TipoResolucion.UNIFICA_CRITERIO)
        ]
    )
    entry = reg.get_teac("00/12345/2023")
    assert entry.tier == JurisprudenceTier.TEAC_UNIFICA


def test_tier_for_teac_ordinaria_is_lower() -> None:
    reg = JurisprudenceRegistry.from_items(
        teac_resoluciones=[
            _make_teac(numero="00/00001/2023", tipo=TipoResolucion.ORDINARIA)
        ]
    )
    entry = reg.get_teac("00/00001/2023")
    assert entry.tier == JurisprudenceTier.TEAC_ORDINARIA


def test_tier_hierarchy_is_consistent() -> None:
    """Garantía estructural: TC > TS > AN > TSJ > AP y TEAC unifica
    entre TS y AN para reflejar su peso AEAT (art. 242 LGT)."""
    assert JurisprudenceTier.TC < JurisprudenceTier.TS
    assert JurisprudenceTier.TS < JurisprudenceTier.TEAC_UNIFICA
    assert JurisprudenceTier.TEAC_UNIFICA < JurisprudenceTier.AN
    assert JurisprudenceTier.AN < JurisprudenceTier.TSJ
    assert JurisprudenceTier.TSJ < JurisprudenceTier.AP
    assert JurisprudenceTier.TEAC_UNIFICA < JurisprudenceTier.TEAC_EXTIENDE
    assert JurisprudenceTier.TEAC_EXTIENDE < JurisprudenceTier.TEAC_ORDINARIA
    # Cada miembro debe tener su propio nombre (no aliases del IntEnum).
    assert JurisprudenceTier.TS.name == "TS"
    assert JurisprudenceTier.TEAC_UNIFICA.name == "TEAC_UNIFICA"


# ---------- DoctrineWeight (reiterada / vinculante) ----------


def test_tc_sentencia_is_binding() -> None:
    reg = JurisprudenceRegistry.from_items(
        sentencias=[
            _make_sentencia(
                ecli="ECLI:ES:TC:2021:182",
                organo=Organo.TC,
                tribunal_codigo="TC",
            )
        ]
    )
    assert (
        reg.get_sentencia("ECLI:ES:TC:2021:182").weight == DoctrineWeight.BINDING
    )


def test_two_ts_sentencias_same_asunto_are_consolidated() -> None:
    """Dos sentencias TS de la misma sala, mismo sentido y mismo asunto-key
    se marcan como doctrina reiterada (CONSOLIDATED)."""
    reg = JurisprudenceRegistry.from_items(
        sentencias=[
            _make_sentencia(
                ecli="ECLI:ES:TS:2024:1234",
                resumen="Dietas IRPF carga prueba trabajador desplazado",
            ),
            _make_sentencia(
                ecli="ECLI:ES:TS:2024:5678",
                resumen="Dietas IRPF carga prueba trabajador desplazado",
            ),
        ]
    )
    e1 = reg.get_sentencia("ECLI:ES:TS:2024:1234")
    e2 = reg.get_sentencia("ECLI:ES:TS:2024:5678")
    assert e1.weight == DoctrineWeight.CONSOLIDATED
    assert e2.weight == DoctrineWeight.CONSOLIDATED


def test_single_ts_sentencia_is_isolated() -> None:
    reg = JurisprudenceRegistry.from_items(
        sentencias=[_make_sentencia(ecli="ECLI:ES:TS:2024:1234")]
    )
    assert (
        reg.get_sentencia("ECLI:ES:TS:2024:1234").weight
        == DoctrineWeight.ISOLATED
    )


def test_teac_unifica_is_binding() -> None:
    reg = JurisprudenceRegistry.from_items(teac_resoluciones=[_make_teac()])
    assert reg.get_teac("00/12345/2023").weight == DoctrineWeight.BINDING


def test_teac_extiende_is_binding() -> None:
    reg = JurisprudenceRegistry.from_items(
        teac_resoluciones=[
            _make_teac(
                numero="00/00001/2024",
                tipo=TipoResolucion.EXTIENDE_EFECTOS,
            )
        ]
    )
    assert (
        reg.get_teac("00/00001/2024").weight == DoctrineWeight.BINDING
    )


def test_teac_ordinaria_is_isolated() -> None:
    reg = JurisprudenceRegistry.from_items(
        teac_resoluciones=[
            _make_teac(
                numero="00/00001/2024",
                tipo=TipoResolucion.ORDINARIA,
            )
        ]
    )
    assert (
        reg.get_teac("00/00001/2024").weight == DoctrineWeight.ISOLATED
    )


# ---------- from_disk ----------


def test_from_disk_loads_existing_dirs(tmp_path: Path) -> None:
    """Acepta directorios con la estructura del corpus y produce un registry
    válido. Directorios inexistentes se tratan como vacíos."""
    jurisprudencia_dir = tmp_path / "jurisprudencia" / "ts" / "2024"
    jurisprudencia_dir.mkdir(parents=True)
    sentencia = _make_sentencia(ecli="ECLI:ES:TS:2024:1234")
    (jurisprudencia_dir / "ECLI-ES-TS-2024-1234.json").write_text(
        _dump(sentencia.to_dict()), encoding="utf-8"
    )

    dgt_dir = tmp_path / "dgt_consultas" / "2024"
    dgt_dir.mkdir(parents=True)
    dgt = _make_dgt("V0123-24")
    (dgt_dir / "V0123-24.json").write_text(
        _dump(dgt.to_dict()), encoding="utf-8"
    )

    teac_dir = tmp_path / "teac_resoluciones" / "teac" / "2023"
    teac_dir.mkdir(parents=True)
    teac = _make_teac()
    (teac_dir / "00_12345_2023.json").write_text(
        _dump(teac.to_dict()), encoding="utf-8"
    )

    reg = JurisprudenceRegistry.from_disk(
        jurisprudencia_dir=tmp_path / "jurisprudencia",
        dgt_dir=tmp_path / "dgt_consultas",
        teac_dir=tmp_path / "teac_resoluciones",
    )
    assert reg.knows_ecli("ECLI:ES:TS:2024:1234")
    assert reg.knows_dgt("V0123-24")
    assert reg.knows_teac("00/12345/2023")
    assert reg.total == 3


def test_from_disk_missing_dirs_yield_empty_registry(tmp_path: Path) -> None:
    reg = JurisprudenceRegistry.from_disk(
        jurisprudencia_dir=tmp_path / "nope",
        dgt_dir=tmp_path / "nope2",
        teac_dir=tmp_path / "nope3",
    )
    assert reg.total == 0
    assert not reg


def _dump(data: dict) -> str:
    import json

    return json.dumps(data, ensure_ascii=False, default=str)
