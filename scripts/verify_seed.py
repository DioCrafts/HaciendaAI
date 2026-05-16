"""Verificador del corpus semilla contra el BOE.

Para cada `Source` con `boe_id = "BOE-A-..."` y `content_hash` declarado:

1. Descarga el texto consolidado de la norma (`legislacion-consolidada` API).
2. Localiza el bloque `<bloque id="..."` correspondiente al pinpoint.
3. Selecciona la versión vigente en la fecha de referencia
   (`Deduction.last_reviewed_at` si está, si no `today`).
4. Extrae el texto normativo (excluye `<p class="nota_pie*">`, que es
   metadato editorial: el histórico de modificaciones añadido por BOE).
5. Normaliza espacios y calcula SHA-256.
6. Compara con `Source.content_hash`.

Si se pasa `--report PATH`, el script escribe un JSON con la lista
estructurada de `DriftItem`, el análisis de impacto (qué deducciones y
escalas citan cada fuente con drift) y, si se pasa `--include-regional`,
también los enlaces a boletines autonómicos que devuelvan 404/5xx. El
workflow `verify-seed.yml` consume ese JSON para abrir un issue
contextual con la sección de impacto.

Uso:
    python scripts/verify_seed.py                    # verifica todo
    python scripts/verify_seed.py --update           # rellena hashes que falten
    python scripts/verify_seed.py --cache .cache/boe # cache custom
    python scripts/verify_seed.py path/to/file.json  # solo un archivo
    python scripts/verify_seed.py --report out.json  # emite reporte JSON
    python scripts/verify_seed.py --include-regional # añade chequeo URLs CCAA

Códigos de salida:
    0 — sin drift y sin errores.
    1 — drift detectado, hashes faltantes o enlaces autonómicos rotos.
    2 — error de red, parsing o entrada inválida.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

# Importes pos-sys.path para que el script funcione sin instalación previa.
from hacienda_ai.deductions import load_deductions  # noqa: E402
from hacienda_ai.irpf import load_tax_scales  # noqa: E402
from hacienda_ai.rag.impact import (  # noqa: E402
    DriftItem,
    analyze_impact,
    write_report_json,
)
from hacienda_ai.rag.sources import check_regional_urls  # noqa: E402

DEFAULT_DEDUCTIONS_DIR = REPO_ROOT / "src" / "hacienda_ai" / "data" / "deductions"
DEFAULT_SCALES_DIR = REPO_ROOT / "src" / "hacienda_ai" / "data" / "escalas"
DEFAULT_CACHE_DIR = REPO_ROOT / ".cache" / "boe"
BOE_API = (
    "https://www.boe.es/datosabiertos/api/legislacion-consolidada/id/{boe_id}/texto"
)
USER_AGENT = "hacienda-ai-verify-seed/0.1 (+https://github.com/DioCrafts/HaciendaAI)"

# Clases CSS de párrafo que sí son texto normativo. Excluimos `nota_pie*`,
# que es metadato editorial añadido por BOE con el histórico de
# modificaciones; cualquier referencia nueva ahí dispararía falso drift.
NON_NORMATIVE_CLASSES = re.compile(r"^nota_pie(_\d+)?$")


class BoeFetchError(RuntimeError):
    """Error descargando o parseando un documento BOE."""


def fetch_consolidated(boe_id: str, cache_dir: Path) -> str:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{boe_id}.xml"
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")
    url = BOE_API.format(boe_id=boe_id)
    req = urllib.request.Request(
        url, headers={"Accept": "application/xml", "User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise BoeFetchError(f"no se pudo descargar {boe_id}: {exc}") from exc
    cache_file.write_text(payload, encoding="utf-8")
    time.sleep(0.2)
    return payload


def parse_article_id(article: str) -> str | None:
    """Convierte 'art. 81 bis' → 'a81bis', 'DT 15ª' → 'dtdecimoquinta' aprox.

    Para `DA`/`DT` con numeración ordinal en castellano, BOE usa nombres como
    `dtdecimoquinta`. Esa correspondencia no es invertible sin diccionario,
    así que para esas se puede pasar el id BOE directamente con prefijo
    `boe:` (p.ej. `article = "boe:dtdecimoquinta"`).
    """
    s = article.strip().lower()
    if s.startswith("boe:"):
        return s.removeprefix("boe:")
    s = re.sub(r"art[íi]culo|art\.|art|ª|º", "", s).replace(".", " ").strip()
    m = re.match(r"^(\d+)\s*(bis|ter|quater|quinquies|sexies)?$", s)
    if m:
        suffix = m.group(2) or ""
        return f"a{m.group(1)}{suffix}"
    return None


def find_block(xml: str, block_id: str) -> str:
    pat = re.compile(
        rf'<bloque\s+id="{re.escape(block_id)}"\s+tipo="precepto"[^>]*>(.*?)</bloque>',
        re.DOTALL,
    )
    m = pat.search(xml)
    if not m:
        raise BoeFetchError(f"bloque '{block_id}' no encontrado")
    return m.group(1)


def select_version(body: str, target: date) -> str:
    """Devuelve el cuerpo de la versión cuyo intervalo de vigencia cubre `target`.

    Si ninguna versión histórica registra `fecha_vigencia`, se devuelve la
    última encontrada como fallback.
    """
    target_str = target.strftime("%Y%m%d")
    chosen: str | None = None
    chosen_from = ""
    for v in re.finditer(r"<version\s+([^>]*)>(.*?)</version>", body, re.DOTALL):
        attrs, content = v.group(1), v.group(2)
        f_match = re.search(r'fecha_vigencia="(\d{8})"', attrs)
        if not f_match:
            continue
        fecha_from = f_match.group(1)
        if fecha_from > target_str:
            continue
        fin_match = re.search(r'fecha_vigencia_fin="(\d{8})"', attrs)
        if fin_match and fin_match.group(1) < target_str:
            continue
        if chosen is None or fecha_from > chosen_from:
            chosen = content
            chosen_from = fecha_from
    if chosen is None:
        # Fallback: última versión.
        versions = re.findall(r"<version\s+[^>]*>(.*?)</version>", body, re.DOTALL)
        if not versions:
            raise BoeFetchError("bloque sin elementos <version>")
        chosen = versions[-1]
    return chosen


def normalize_version_text(version_body: str) -> str:
    """Extrae texto plano normativo de un cuerpo `<version>`.

    Concatena todos los `<p>` salvo los de clase `nota_pie*`. Dentro de cada
    `<p>` elimina etiquetas anidadas (a, b, sup, etc.) y colapsa espacios.
    """
    out: list[str] = []
    for m in re.finditer(r'<p\s+class="([^"]+)"[^>]*>(.*?)</p>', version_body, re.DOTALL):
        cls = m.group(1)
        if NON_NORMATIVE_CLASSES.match(cls):
            continue
        inner = re.sub(r"<[^>]+>", "", m.group(2))
        inner = re.sub(r"\s+", " ", inner).strip()
        if inner:
            out.append(inner)
    return "\n".join(out)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_hash(
    xml: str,
    block_id: str,
    target: date,
) -> tuple[str, str]:
    body = find_block(xml, block_id)
    version_body = select_version(body, target)
    text = normalize_version_text(version_body)
    return sha256(text), text


def iter_files(root: Path | None, explicit: list[Path]) -> Iterable[Path]:
    if explicit:
        for p in explicit:
            if not p.exists():
                raise BoeFetchError(f"archivo no encontrado: {p}")
            yield p
        return
    assert root is not None
    yield from sorted(root.glob("*.json"))


def reference_date(entry: dict[str, object]) -> date:
    raw = entry.get("last_reviewed_at")
    if isinstance(raw, str) and raw:
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise BoeFetchError(
                f"deducción {entry.get('id')!r}: last_reviewed_at inválido: {raw}"
            ) from exc
    return date.today()


def verify_file(
    path: Path,
    cache_dir: Path,
    update: bool,
    drift_items: list[DriftItem] | None = None,
) -> tuple[int, int, int, int, bool]:
    """Verifica un fichero JSON. Devuelve (ok, drift, skipped, errors, changed).

    Si `drift_items` se pasa (no None), cada divergencia detectada se anota
    como un `DriftItem` en esa lista para el reporte posterior.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict):
        # Aceptamos los dos formatos del repo: corpus de deducciones
        # (`{"deductions": [...]}`) y corpus de escalas progresivas
        # (`{"scales": [...]}`). Cada entrada debe llevar su propio `sources`,
        # con el mismo formato que las deducciones, así que el verificador
        # las trata de forma uniforme.
        entries = raw.get("deductions") or raw.get("scales") or []
    else:
        raise BoeFetchError(f"{path}: estructura JSON inesperada")
    if not isinstance(entries, list):
        raise BoeFetchError(f"{path}: estructura JSON inesperada")

    ok = drift = skipped = errors = 0
    changed = False

    for entry in entries:
        ref_date = reference_date(entry)
        ded_id = entry.get("id", "<sin id>")
        for source in entry.get("sources", []):
            boe_id = source.get("boe_id")
            article = source.get("article")
            declared = source.get("content_hash")
            if not boe_id or not boe_id.startswith("BOE-A-"):
                skipped += 1
                continue
            if not article:
                print(f"  · {ded_id}: source sin article (skip)")
                skipped += 1
                continue
            block_id = parse_article_id(article)
            if block_id is None:
                print(f"  · {ded_id}: artículo no parseable {article!r} (skip)")
                skipped += 1
                continue
            try:
                xml = fetch_consolidated(boe_id, cache_dir)
                computed, _ = compute_hash(xml, block_id, ref_date)
            except BoeFetchError as exc:
                print(f"  ✗ {ded_id} @ {boe_id}/{block_id}: {exc}")
                errors += 1
                continue
            if declared is None:
                if update:
                    source["content_hash"] = computed
                    changed = True
                    print(f"  + {ded_id} @ {block_id}: hash añadido {computed[:16]}…")
                    ok += 1
                else:
                    print(f"  ! {ded_id} @ {block_id}: hash ausente — calculado {computed}")
                    drift += 1
                    if drift_items is not None:
                        drift_items.append(
                            DriftItem(
                                boe_id=boe_id,
                                article=article,
                                declared_hash=None,
                                computed_hash=computed,
                                deduction_id=str(ded_id),
                            )
                        )
            elif computed != declared:
                print(f"  ✗ {ded_id} @ {boe_id}/{block_id}: DRIFT")
                print(f"      declarado:  {declared}")
                print(f"      calculado:  {computed}")
                drift += 1
                if drift_items is not None:
                    drift_items.append(
                        DriftItem(
                            boe_id=boe_id,
                            article=article,
                            declared_hash=declared,
                            computed_hash=computed,
                            deduction_id=str(ded_id),
                        )
                    )
            else:
                ok += 1
                print(f"  ✓ {ded_id} @ {boe_id}/{block_id}")

    if update and changed:
        path.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return ok, drift, skipped, errors, changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verificador BOE del corpus semilla.")
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="archivos JSON a verificar (por defecto, todos en data/deductions)",
    )
    parser.add_argument("--deductions-dir", type=Path, default=DEFAULT_DEDUCTIONS_DIR)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument(
        "--update",
        action="store_true",
        help="reescribe los JSON rellenando content_hash que falten",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help=(
            "Si se indica, escribe un JSON con los DriftItem detectados y "
            "el análisis de impacto (qué deducciones/escalas citan cada "
            "fuente afectada). Lo consume el workflow para construir el "
            "body del issue."
        ),
    )
    parser.add_argument(
        "--include-regional",
        action="store_true",
        help=(
            "Verifica también las URLs de boletines autonómicos declarados "
            "en las fuentes (BOCM, DOGC, DOG…). Reporta solo 404/5xx/timeout."
        ),
    )
    args = parser.parse_args(argv)

    try:
        targets = list(iter_files(args.deductions_dir, args.files))
    except BoeFetchError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not targets:
        print(f"No hay archivos en {args.deductions_dir}")
        return 0

    total = {"ok": 0, "drift": 0, "skipped": 0, "errors": 0}
    drift_items: list[DriftItem] = []
    for path in targets:
        print(f"\n→ {path.relative_to(REPO_ROOT) if REPO_ROOT in path.parents else path}")
        try:
            ok, drift, skipped, errors, _ = verify_file(
                path, args.cache, args.update, drift_items=drift_items
            )
        except BoeFetchError as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)
            total["errors"] += 1
            continue
        total["ok"] += ok
        total["drift"] += drift
        total["skipped"] += skipped
        total["errors"] += errors

    print(
        "\nResumen: "
        f"ok={total['ok']} drift={total['drift']} "
        f"skipped={total['skipped']} errors={total['errors']}"
    )

    broken_urls: list = []
    if args.include_regional:
        try:
            corpus = load_deductions(args.deductions_dir)
            scales = load_tax_scales(DEFAULT_SCALES_DIR)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR al cargar corpus para chequeo regional: {exc}", file=sys.stderr)
            total["errors"] += 1
        else:
            print("\n→ Chequeo de URLs autonómicas")
            broken_urls = check_regional_urls(corpus, scales)
            if broken_urls:
                for b in broken_urls:
                    print(f"  ✗ {b.boe_id} → {b.url} (status={b.status_code}, error={b.error})")
            else:
                print("  ✓ todas las URLs autonómicas responden")

    if args.report is not None:
        try:
            corpus = load_deductions(args.deductions_dir)
            scales = load_tax_scales(DEFAULT_SCALES_DIR)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR al cargar corpus para reporte: {exc}", file=sys.stderr)
            return 2
        report = analyze_impact(drift_items, broken_urls, corpus, scales)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        write_report_json(report, args.report)
        print(f"\nReporte de impacto escrito en {args.report}")

    if total["errors"] > 0:
        return 2
    if total["drift"] > 0 or broken_urls:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
