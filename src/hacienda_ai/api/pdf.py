"""Render PDF de un informe de evaluación.

El PDF está pensado para incorporarse al expediente del cliente: cabecera,
datos del expediente, tabla de deducciones con cita pinpoint y versión
aplicable a la fecha del devengo, y un pie firmable con el SHA-256
agregado del corpus + versión del motor + timestamp.

No hay generación de texto libre, solo serialización de los hechos que el
motor ya tiene en `data` (la misma respuesta que devuelve POST
/evaluations).
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from typing import Any

from weasyprint import HTML  # type: ignore[import-untyped]

STATUS_LABEL = {
    "applies": "Aplica",
    "does_not_apply": "No aplica",
    "missing_data": "Faltan datos",
    "missing_evidence": "Faltan justificantes",
    "pending_validation": "Pendiente de validar",
}

CONFIDENCE_LABEL = {
    "alta": "Confianza alta",
    "media": "Confianza media",
    "baja": "Confianza baja",
}


def _eur(amount: float) -> str:
    """Formato monetario español con miles y decimales."""
    integer, decimal = f"{amount:,.2f}".split(".")
    integer = integer.replace(",", ".")
    return f"{integer},{decimal} €"


def _render_sources(sources: list[dict[str, Any]]) -> str:
    items: list[str] = []
    for s in sources:
        boe_id = escape(s.get("boe_id") or "—")
        article = escape(s.get("article") or "")
        paragraph = s.get("paragraph")
        label = f"{boe_id} {article}".strip()
        if paragraph:
            label += f" §{escape(paragraph)}"
        href = s.get("pinpoint_url") or s.get("url")
        if href:
            items.append(f'<a href="{escape(href)}">{label}</a>')
        else:
            items.append(label)
    return "<br>".join(items)


def _render_versions(versions: list[dict[str, Any]]) -> str:
    if not versions:
        return ""
    out: list[str] = []
    for v in versions:
        boe_id = escape(v["boe_id"])
        eff_from = escape(v["effective_from"])
        eff_to = escape(v["effective_to"]) if v.get("effective_to") else "hoy"
        mod = v.get("modified_by_boe_id")
        mod_suffix = f" · mod. {escape(mod)}" if mod else ""
        out.append(
            f'<span class="version">Redacción {boe_id}: {eff_from} → {eff_to}{mod_suffix}</span>'
        )
    return "<br>".join(out)


def _render_row(ev: dict[str, Any]) -> str:
    status = ev["status"]
    status_label = STATUS_LABEL.get(status, status)
    amount = (
        f'<td class="amount">{_eur(ev["estimated_amount"])}</td>'
        if ev["estimated_amount"] > 0
        else '<td class="amount muted">—</td>'
    )
    reason = escape(ev.get("reason") or "")
    missing_fields = ev.get("missing_fields", []) or []
    missing_documents = ev.get("missing_documents", []) or []
    extras: list[str] = []
    if missing_fields:
        extras.append("<em>Faltan datos:</em> " + ", ".join(map(escape, missing_fields)))
    if missing_documents:
        extras.append("<em>Faltan justificantes:</em> " + ", ".join(map(escape, missing_documents)))
    reason_block = reason + (
        "<br>" + "<br>".join(extras) if extras else ""
    )
    return (
        f"<tr>"
        f'<td>{escape(ev["deduction_name"])}<br>'
        f'<small class="muted">{escape(ev["deduction_id"])}</small></td>'
        f'<td><span class="status status-{status}">{escape(status_label)}</span></td>'
        f"{amount}"
        f'<td class="risk risk-{escape(ev["risk_level"])}">{escape(ev["risk_level"])}</td>'
        f'<td><span class="conf conf-{escape(ev["confidence"])}">{escape(ev["confidence"])}</span></td>'
        f'<td>{reason_block}</td>'
        f'<td>{_render_sources(ev.get("sources", []))}'
        f"{('<br>' + _render_versions(ev.get('applicable_versions', []))) if ev.get('applicable_versions') else ''}"
        f"</td>"
        f"</tr>"
    )


def _profile_summary(profile: dict[str, Any]) -> str:
    keys = [
        ("Ejercicio fiscal", profile.get("tax_year")),
        ("CCAA de residencia", profile.get("region")),
        ("Modo de declaración", profile.get("filing_mode")),
        ("Fecha del devengo", profile.get("devengo_date") or "31-dic"),
    ]
    items = "".join(
        f'<dt>{escape(str(k))}</dt><dd>{escape(str(v) if v is not None else "—")}</dd>'
        for k, v in keys
    )
    return f'<dl class="summary">{items}</dl>'


CSS = """
@page {
  size: A4;
  margin: 16mm 14mm 22mm 14mm;
  @bottom-left {
    content: "HaciendaAI — Informe IRPF generado " counter(page) " / " counter(pages);
    font-size: 8pt; color: #555;
  }
  @bottom-right {
    content: "No sustituye a asesor colegiado · verificar citas en BOE antes de presentar";
    font-size: 8pt; color: #555;
  }
}
* { box-sizing: border-box; }
body { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
       font-size: 9pt; color: #1a1a1a; line-height: 1.35; }
h1 { font-size: 14pt; margin: 0 0 4pt 0; }
h2 { font-size: 11pt; margin: 12pt 0 4pt 0; color: #2657c6; }
.disclaimer { background: #fff4cc; border-left: 4px solid #d6a900;
              padding: 6pt 10pt; margin: 6pt 0 10pt 0; font-size: 8.5pt; }
.summary { display: grid; grid-template-columns: 1fr 1fr; gap: 2pt 14pt; margin: 4pt 0; }
.summary dt { font-weight: 600; }
.summary dd { margin: 0; }
table { width: 100%; border-collapse: collapse; font-size: 8pt; margin-top: 4pt; }
th, td { text-align: left; padding: 4pt 5pt; border-bottom: 1px solid #e3e3e3;
         vertical-align: top; word-wrap: break-word; }
th { background: #f6f7f8; font-weight: 600; }
td.amount { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
td.muted, small.muted { color: #777; }
.status, .conf { font-weight: 600; padding: 1pt 4pt; border-radius: 3pt;
                 display: inline-block; white-space: nowrap; font-size: 7.5pt; }
.status-applies { background: #d6f1d6; color: #195e19; }
.status-does_not_apply { background: #eee; color: #555; }
.status-missing_data, .status-missing_evidence { background: #ffe7c6; color: #8a4d00; }
.status-pending_validation { background: #ffd6d6; color: #8a1a1a; }
.risk-low { color: #195e19; }
.risk-medium { color: #8a4d00; }
.risk-high { color: #8a1a1a; }
.conf-alta { background: #d6f1d6; color: #195e19; }
.conf-media { background: #ffe7c6; color: #8a4d00; }
.conf-baja { background: #ffd6d6; color: #8a1a1a; }
.version { color: #555; font-style: italic; font-size: 7.5pt; }
.signature { margin-top: 10pt; padding: 6pt 10pt; border-top: 1pt solid #999;
             font-size: 7.5pt; color: #555; font-family: ui-monospace, monospace; }
a { color: #2657c6; text-decoration: none; }
"""


def render_evaluation_report_html(evaluation: dict[str, Any]) -> str:
    profile = evaluation.get("profile", {})
    rows = "".join(_render_row(ev) for ev in evaluation["evaluations"])
    summary = _profile_summary(
        {
            "tax_year": profile.get("tax_year"),
            "region": profile.get("region"),
            "filing_mode": profile.get("filing_mode"),
            "devengo_date": evaluation["devengo_date"],
        }
    )
    corpus = evaluation["corpus"]
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    eid = escape(evaluation["evaluation_id"])
    evaluated_at = escape(evaluation["evaluated_at"])
    disclaimer = escape(evaluation["disclaimer"])
    last_reviewed = escape(str(corpus.get("last_reviewed_at") or "—"))
    engine_version = escape(corpus["engine_version"])
    fingerprint = escape(corpus.get("fingerprint_sha256", "—"))
    header_block = (
        '<p><small class="muted">Evaluación nº '
        f"<strong>{eid}</strong> · evaluado el {evaluated_at}</small></p>"
    )
    signature_block = (
        f'<div class="signature">'
        f"Corpus: {corpus['count']} entradas · última revisión {last_reviewed} · "
        f"motor v{engine_version}<br>"
        f"SHA-256 agregado del corpus: <strong>{fingerprint}</strong><br>"
        f"Generado por HaciendaAI el {generated_at}"
        f"</div>"
    )
    return f"""<!doctype html>
<html lang="es">
<head><meta charset="utf-8"><title>HaciendaAI — Informe de evaluación IRPF</title>
<style>{CSS}</style></head>
<body>
<h1>HaciendaAI — Informe de evaluación IRPF (borrador)</h1>
<div class="disclaimer">{disclaimer}</div>
<h2>Datos del expediente</h2>
{summary}
{header_block}

<h2>Deducciones, reducciones y exenciones evaluadas</h2>
<table>
<thead><tr>
<th>Concepto</th><th>Estado</th><th>Importe est.</th>
<th>Riesgo</th><th>Confianza</th><th>Motivo</th><th>Fuente y vigencia</th>
</tr></thead>
<tbody>{rows}</tbody>
</table>

{signature_block}
</body></html>
"""


def render_evaluation_report_pdf(evaluation: dict[str, Any]) -> bytes:
    """Renderiza el informe a PDF (A4). El resultado se devuelve en bytes
    con la cabecera mágica `%PDF-...`."""
    html_str = render_evaluation_report_html(evaluation)
    pdf_bytes: bytes = HTML(string=html_str).write_pdf()
    return pdf_bytes
