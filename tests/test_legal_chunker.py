"""Tests del chunking jurídico jerárquico por artículo/apartado."""

from __future__ import annotations

from datetime import date

from hacienda_ai.rag.legal_chunker import (
    HierarchyContext,
    HierarchyKind,
    build_legal_chunks,
    iter_structural_blocks,
    split_article_into_apartados,
)
from hacienda_ai.rag.legal_chunker.hierarchy import (
    classify_structural_kind,
    iter_precepts_with_context,
)
from hacienda_ai.rag.vector import SourceType

# ---------- Fixture XML mínimo (LIRPF estilizado) ----------

# ruff: noqa: E501
LIRPF_MINI = """<?xml version="1.0" encoding="UTF-8"?>
<legislacion-consolidada>
<texto>
<bloque id="t3" tipo="estructura">
<version fecha_vigencia="20070101"><p class="parrafo">TÍTULO III. DETERMINACIÓN DE LA RENTA</p></version>
</bloque>
<bloque id="c1" tipo="estructura">
<version fecha_vigencia="20070101"><p class="parrafo">CAPÍTULO I. RENDIMIENTOS DEL TRABAJO</p></version>
</bloque>
<bloque id="s1" tipo="estructura">
<version fecha_vigencia="20070101"><p class="parrafo">Sección 1ª Disposiciones generales</p></version>
</bloque>
<bloque id="a17" tipo="precepto">
<version fecha_vigencia="20070101"><p class="parrafo">Artículo 17. Rendimientos íntegros del trabajo.</p><p class="parrafo">1. Se considerarán rendimientos íntegros del trabajo todas las contraprestaciones que deriven, directa o indirectamente, del trabajo personal o de la relación laboral o estatutaria.</p><p class="parrafo">2. En particular, tendrán la consideración de rendimientos del trabajo: a) Los sueldos y salarios. b) Las prestaciones por desempleo. c) Las remuneraciones en concepto de gastos de representación.</p></version>
</bloque>
<bloque id="a19" tipo="precepto">
<version fecha_vigencia="20150101"><p class="parrafo">Artículo 19. Gastos deducibles.</p><p class="parrafo">1. El rendimiento neto del trabajo será el resultado de disminuir el rendimiento íntegro en el importe de los gastos deducibles.</p><p class="parrafo">2. Tendrán la consideración de gastos deducibles exclusivamente los siguientes: a) Las cotizaciones a la Seguridad Social. b) Las detracciones por derechos pasivos. e) Los gastos de defensa jurídica derivados directamente de litigios suscitados en la relación del contribuyente con la persona de la que percibe los rendimientos, con el límite de 300 euros anuales.</p></version>
</bloque>
<bloque id="c2" tipo="estructura">
<version fecha_vigencia="20070101"><p class="parrafo">CAPÍTULO II. RENDIMIENTOS DEL CAPITAL</p></version>
</bloque>
<bloque id="a22" tipo="precepto">
<version fecha_vigencia="20070101"><p class="parrafo">Artículo 22. Rendimientos íntegros del capital inmobiliario.</p><p class="parrafo">Tienen la consideración de rendimientos íntegros del capital inmobiliario los procedentes del arrendamiento o de la constitución o cesión de derechos o facultades de uso o disfrute sobre bienes inmuebles.</p></version>
</bloque>
</texto>
</legislacion-consolidada>"""


# ---------- hierarchy.py ----------


def test_iter_structural_blocks_distingue_estructura_y_precepto() -> None:
    blocks = list(iter_structural_blocks(LIRPF_MINI))
    # 4 estructura (t3, c1, s1, c2) + 3 preceptos (a17, a19, a22).
    estructura = [b for b in blocks if not b.is_precept]
    preceptos = [b for b in blocks if b.is_precept]
    assert len(estructura) == 4
    assert len(preceptos) == 3
    assert {b.block_id for b in preceptos} == {"a17", "a19", "a22"}


def test_classify_structural_kind_por_prefijo() -> None:
    assert classify_structural_kind("t3", "TÍTULO III") == HierarchyKind.TITULO
    assert classify_structural_kind("c1", "CAPÍTULO I") == HierarchyKind.CAPITULO
    assert classify_structural_kind("s1", "Sección 1ª") == HierarchyKind.SECCION
    assert (
        classify_structural_kind("ss2", "Subsección 2ª")
        == HierarchyKind.SUBSECCION
    )
    assert classify_structural_kind("l1", "LIBRO I") == HierarchyKind.LIBRO


def test_classify_structural_kind_fallback_por_keyword() -> None:
    # Id que no encaja con prefijo (ej. "x1"), pero el título sí.
    assert (
        classify_structural_kind("x1", "Sección quinta")
        == HierarchyKind.SECCION
    )


def test_classify_no_false_positive_con_tarifa() -> None:
    """`tarifa` no debe clasificarse como Título por su prefijo `t`."""
    assert (
        classify_structural_kind("tarifa", "Tarifa")
        == HierarchyKind.OTRO
    )


def test_iter_precepts_with_context_propaga_jerarquia() -> None:
    pairs = list(iter_precepts_with_context(LIRPF_MINI))
    assert len(pairs) == 3
    by_id = {p.block_id: ctx for p, ctx in pairs}

    # a17 y a19 están bajo Título III / Capítulo I / Sección 1ª.
    assert "DETERMINACIÓN" in (by_id["a17"].titulo or "")
    assert "TRABAJO" in (by_id["a17"].capitulo or "")
    assert "Disposiciones" in (by_id["a17"].seccion or "")
    assert by_id["a19"].capitulo == by_id["a17"].capitulo

    # a22 está bajo Capítulo II (no Capítulo I), Título III heredado.
    assert "CAPITAL" in (by_id["a22"].capitulo or "")
    assert by_id["a22"].titulo == by_id["a17"].titulo
    # Sección se resetea al cambiar de capítulo.
    assert by_id["a22"].seccion is None


def test_hierarchy_context_with_block_resetea_descendientes() -> None:
    ctx = HierarchyContext(
        titulo="Título Original",
        capitulo="Capítulo X",
        seccion="Sección Y",
        subseccion="Sub Z",
    )
    # Pseudo-bloque de tipo Capítulo: resetea Sección y Subsección.
    from hacienda_ai.rag.legal_chunker.hierarchy import StructuralBlock

    nuevo_cap = StructuralBlock(
        block_id="c5",
        is_precept=False,
        kind=HierarchyKind.CAPITULO,
        title="Capítulo Nuevo",
        raw_body="",
    )
    ctx2 = ctx.with_block(nuevo_cap)
    assert ctx2.titulo == "Título Original"  # heredado.
    assert ctx2.capitulo == "Capítulo Nuevo"
    assert ctx2.seccion is None
    assert ctx2.subseccion is None


def test_hierarchy_context_as_tuple_omite_none() -> None:
    ctx = HierarchyContext(titulo="T", capitulo="C")
    assert ctx.as_tuple() == ("T", "C")


# ---------- splitter.py ----------


def test_split_article_apartados_numerados() -> None:
    text = (
        "Artículo 19. Gastos deducibles.\n\n"
        "1. El rendimiento neto del trabajo será el resultado de "
        "disminuir el rendimiento íntegro en gastos deducibles.\n\n"
        "2. Tendrán la consideración de gastos deducibles los siguientes."
    )
    apartados = split_article_into_apartados(text)
    assert len(apartados) == 2
    assert apartados[0].numero == "1"
    assert apartados[1].numero == "2"
    # El encabezado del artículo no debe colarse en el primer apartado.
    assert "Gastos deducibles" not in apartados[0].texto


def test_split_article_apartados_con_letras() -> None:
    text = (
        "Artículo 19. Gastos deducibles.\n\n"
        "2. Tendrán la consideración de gastos deducibles los siguientes:\n\n"
        "a) Las cotizaciones a la Seguridad Social.\n\n"
        "b) Las detracciones por derechos pasivos.\n\n"
        "e) Los gastos de defensa jurídica con el límite de 300 euros."
    )
    apartados = split_article_into_apartados(text)
    # Esperamos: preámbulo del apartado 2 + tres letras a/b/e.
    numeros = [a.numero for a in apartados]
    assert "2" in numeros  # preámbulo del apartado 2.
    assert "2.a)" in numeros
    assert "2.b)" in numeros
    assert "2.e)" in numeros


def test_split_article_sin_apartados_devuelve_uno_solo() -> None:
    text = "Artículo 22. Concepto.\n\nTienen la consideración de rendimientos del capital inmobiliario los procedentes del arrendamiento."
    apartados = split_article_into_apartados(text)
    assert len(apartados) == 1
    assert apartados[0].numero is None
    assert "arrendamiento" in apartados[0].texto


def test_split_article_vacio_devuelve_lista_vacia() -> None:
    assert split_article_into_apartados("") == []


# ---------- builder.py ----------


def test_build_legal_chunks_produce_chunks_por_apartado() -> None:
    chunks = build_legal_chunks(
        xml=LIRPF_MINI,
        boe_id="BOE-A-2006-20764",
        kind="ley",
        version_effective_from=date(2015, 1, 1),
        version_effective_to=None,
        reference_date=date(2016, 1, 1),
    )
    # Verificamos que cada apartado canónico está cubierto.
    apartados = {c.metadata.get("apartado") for c in chunks}
    # a17 tiene 1 y 2 (con letras a/b/c dentro de 2).
    # a19 tiene 1 y 2 (con a/b/e dentro de 2).
    # a22 no tiene apartados.
    assert "1" in apartados
    assert "2" in apartados or any(
        a is not None and a.startswith("2.") for a in apartados
    )
    # El apartado 2.e) de art.19 (gastos de defensa jurídica) debe existir.
    assert any(a == "2.e)" for a in apartados)


def test_build_legal_chunks_metadata_jerarquica_completa() -> None:
    chunks = build_legal_chunks(
        xml=LIRPF_MINI,
        boe_id="BOE-A-2006-20764",
        kind="ley",
        version_effective_from=date(2015, 1, 1),
        version_effective_to=None,
        reference_date=date(2016, 1, 1),
    )
    # Tomamos un chunk de art. 19 apartado 2.e).
    c19 = next(
        c for c in chunks
        if c.metadata.get("articulo") == "art. 19"
        and c.metadata.get("apartado") == "2.e)"
    )
    assert c19.source_type == SourceType.NORMA
    assert c19.metadata["boe_id"] == "BOE-A-2006-20764"
    assert c19.metadata["kind"] == "ley"
    assert c19.metadata["vigencia_desde"] == "2015-01-01"
    assert c19.metadata.get("vigencia_hasta") is None  # vigente sin tope.
    # Jerarquía completa.
    jer = c19.metadata["jerarquia"]
    assert isinstance(jer, list)
    assert any("Título" in p or "TÍTULO" in p for p in jer)
    assert any("Capítulo" in p or "CAPÍTULO" in p for p in jer)
    assert any("Sección" in p for p in jer)


def test_build_legal_chunks_articulo_sin_apartados() -> None:
    """art. 22 no tiene apartados numerados: debe producir 1 chunk con
    `apartado=None` en metadata."""
    chunks = build_legal_chunks(
        xml=LIRPF_MINI,
        boe_id="BOE-A-2006-20764",
        kind="ley",
        version_effective_from=date(2015, 1, 1),
        version_effective_to=None,
        reference_date=date(2016, 1, 1),
    )
    a22_chunks = [c for c in chunks if c.metadata.get("articulo") == "art. 22"]
    assert len(a22_chunks) == 1
    assert "apartado" not in a22_chunks[0].metadata
    assert "arrendamiento" in a22_chunks[0].text.lower()


def test_build_legal_chunks_id_estable_incluye_version() -> None:
    """Reconstruir los chunks de la misma versión debe dar ids idénticos."""
    chunks_a = build_legal_chunks(
        xml=LIRPF_MINI,
        boe_id="BOE-A-2006-20764",
        kind="ley",
        version_effective_from=date(2015, 1, 1),
        version_effective_to=None,
    )
    chunks_b = build_legal_chunks(
        xml=LIRPF_MINI,
        boe_id="BOE-A-2006-20764",
        kind="ley",
        version_effective_from=date(2015, 1, 1),
        version_effective_to=None,
    )
    ids_a = {c.chunk_id for c in chunks_a}
    ids_b = {c.chunk_id for c in chunks_b}
    assert ids_a == ids_b
    # Versiones distintas → ids distintos.
    chunks_c = build_legal_chunks(
        xml=LIRPF_MINI,
        boe_id="BOE-A-2006-20764",
        kind="ley",
        version_effective_from=date(2007, 1, 1),
        version_effective_to=date(2014, 12, 31),
    )
    ids_c = {c.chunk_id for c in chunks_c}
    assert ids_a != ids_c


def test_build_legal_chunks_texto_incluye_cabecera_jerarquica() -> None:
    """El texto a embebido lleva contexto jerárquico + articulo + apartado
    + cuerpo, para que el retrieval localice el chunk correcto incluso
    cuando solo se mencione una palabra clave del contenido."""
    chunks = build_legal_chunks(
        xml=LIRPF_MINI,
        boe_id="BOE-A-2006-20764",
        kind="ley",
        version_effective_from=date(2015, 1, 1),
        version_effective_to=None,
    )
    c19_2e = next(
        c for c in chunks
        if c.metadata.get("articulo") == "art. 19"
        and c.metadata.get("apartado") == "2.e)"
    )
    assert "art. 19" in c19_2e.text
    assert "2.e)" in c19_2e.text
    assert "defensa jurídica" in c19_2e.text


def test_build_legal_chunks_excluye_articulos_sin_version_vigente() -> None:
    """art.19 solo existe desde 2015. Si pedimos referencia 2010, NO
    debe aparecer en chunks."""
    chunks = build_legal_chunks(
        xml=LIRPF_MINI,
        boe_id="BOE-A-2006-20764",
        kind="ley",
        version_effective_from=date(2007, 1, 1),
        version_effective_to=date(2014, 12, 31),
        reference_date=date(2010, 1, 1),
    )
    articulos = {c.metadata.get("articulo") for c in chunks}
    assert "art. 19" not in articulos
    # art.17 y art.22 sí están desde 2007.
    assert "art. 17" in articulos
    assert "art. 22" in articulos


def test_build_legal_chunks_format_articulo() -> None:
    """`a81bis` → `art. 81 bis`, `dadecimoctava` → `DA decimoctava`."""
    from hacienda_ai.rag.legal_chunker.builder import _format_articulo

    assert _format_articulo("a19") == "art. 19"
    assert _format_articulo("a81bis") == "art. 81 bis"
    assert _format_articulo("a23ter") == "art. 23 ter"
    assert _format_articulo("dadecimoctava") == "DA decimoctava"
    assert _format_articulo("dtdecimoquinta") == "DT decimoquinta"
    assert _format_articulo("df1") == "DF 1"
    # Desconocido: tal cual.
    assert _format_articulo("xyz") == "xyz"
