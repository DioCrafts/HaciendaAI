"""Tests del chunker semántico de manuales AEAT."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from hacienda_ai.models import ManualChunk, ManualFuente
from hacienda_ai.rag.manuals import (
    ChunkingConfig,
    StubPdfExtractor,
    chunk_from_structure,
    detect_structure,
)

FIXTURES = Path(__file__).parent / "fixtures" / "manuals"


def _build_chunks(
    *,
    target_words: int = 400,
    max_words: int = 800,
    min_words: int = 50,
) -> list[ManualChunk]:
    pages = StubPdfExtractor().extract(FIXTURES / "manual_irpf_2024_sample.txt")
    root = detect_structure(pages)
    return chunk_from_structure(
        root,
        fuente=ManualFuente.MANUAL_IRPF,
        ejercicio=2024,
        today=date(2024, 9, 1),
        url_fuente=None,
        config=ChunkingConfig(
            min_words=min_words,
            target_words=target_words,
            max_words=max_words,
        ),
    )


def test_chunks_propaga_fuente_y_ejercicio() -> None:
    chunks = _build_chunks()
    assert all(c.fuente == ManualFuente.MANUAL_IRPF for c in chunks)
    assert all(c.ejercicio == 2024 for c in chunks)


def test_chunks_tienen_metadata_jerarquica() -> None:
    chunks = _build_chunks()
    # Al menos uno debe llevar capítulo + sección + subsección.
    has_full = any(
        c.capitulo and c.seccion and c.subseccion for c in chunks
    )
    assert has_full


def test_chunk_id_estable_y_unico() -> None:
    chunks = _build_chunks()
    ids = [c.chunk_id for c in chunks]
    # No hay colisiones de id.
    assert len(set(ids)) == len(ids)
    # Cada id sigue el patrón documentado.
    for cid in ids:
        assert cid.startswith("manual_irpf::2024::")
        assert "::p" in cid


def test_chunks_para_subseccion_1_1_1_contienen_definicion_legal() -> None:
    """La subsección 1.1.1 del fixture habla de 'consideración de rendimientos del trabajo'."""
    chunks = _build_chunks()
    matching = [c for c in chunks if c.subseccion and "1.1.1" in c.subseccion]
    assert len(matching) >= 1
    assert any("consideración" in c.contenido.lower() for c in matching)


def test_chunk_content_hash_es_sha256() -> None:
    chunks = _build_chunks()
    for c in chunks:
        assert len(c.content_hash) == 64


def test_max_words_no_se_excede_para_parrafos_normales() -> None:
    """Con max_words=200, ningún chunk normal supera el límite por mucho."""
    chunks = _build_chunks(target_words=100, max_words=200)
    for c in chunks:
        words = len(c.contenido.split())
        # Permitimos margen del 30% para párrafos individuales largos
        # (la política del chunker prefiere unidad semántica a corte
        # estricto en max_words).
        assert words <= 260, f"chunk con {words} palabras supera max+margen"


def test_target_words_partiendo_subseccion_larga() -> None:
    """Subsecciones largas (>target_words) deben subdividirse en varios chunks."""
    # Con target_words=30 y max_words=60, cada subsección del fixture
    # (con párrafos de ~70 palabras) se subdividirá.
    chunks_small = _build_chunks(target_words=30, max_words=60)
    chunks_large = _build_chunks(target_words=800, max_words=1600)
    # Con configuración pequeña debe haber más chunks que con grande.
    assert len(chunks_small) >= len(chunks_large)


def test_chunk_title_indica_parte_si_subdividido() -> None:
    """Cuando una subsección se parte en varias, el título refleja `(parte X/Y)`."""
    chunks = _build_chunks(target_words=30, max_words=60)
    multipart_titles = [c.titulo for c in chunks if "parte" in c.titulo.lower()]
    # El fixture tiene subsecciones lo bastante largas como para producir
    # al menos un caso multipart con esa configuración tan estrecha.
    assert len(multipart_titles) >= 1


def test_chunker_sin_estructura_devuelve_al_menos_un_chunk() -> None:
    """Texto sin encabezados detectables debe igualmente producir chunks."""
    from hacienda_ai.rag.manuals.pdf_extractor import PageText
    from hacienda_ai.rag.manuals.structure import detect_structure

    pages = [
        PageText(
            page_number=1,
            text=(
                "Texto suelto del manual sin encabezados numerados. "
                "Este párrafo contiene material doctrinal sobre los "
                "rendimientos del capital mobiliario. "
                "Otro párrafo explicando la tributación de las ganancias "
                "patrimoniales según el artículo 33 LIRPF y sus "
                "desarrollos reglamentarios."
            ),
        )
    ]
    root = detect_structure(pages)
    chunks = chunk_from_structure(
        root,
        fuente=ManualFuente.MANUAL_IRPF,
        ejercicio=2023,
        today=date(2024, 9, 1),
    )
    assert len(chunks) >= 1
    # Sin estructura jerárquica: capítulo/sección/subsección son None.
    assert all(c.capitulo is None for c in chunks)
    assert all(c.seccion is None for c in chunks)
