"""Registro auditable de jurisprudencia y doctrina administrativa.

Pareja simétrica a `NormaRegistry` (que indexa BOE-A) pero para la otra
mitad del corpus auditable: sentencias (CENDOJ/TC), consultas vinculantes
de la DGT y resoluciones TEAC/TEAR. Permite al `citation_guard` cruzar
cada cita jurisprudencial (`ECLI:ES:TS:2024:1234`, `V0123-24`,
`00/12345/2023`) contra un corpus real y bloquear las inventadas.

Responsabilidades:

- **Lookup canónico**: ¿está este ECLI/V0123-24/00/12345/2023 en el
  corpus? Sin esto, el LLM puede inventar identificadores plausibles que
  ningún humano detectaría sin abrir CENDOJ/Petete/DYCTEA.
- **Jerarquía doctrinal** (`JurisprudenceTier`): TC > TS > AN > TSJ > AP
  + equivalencias administrativas (TEAC unifica criterio ≈ TS para
  AEAT). Útil para que la respuesta priorice fuentes de mayor peso y el
  reranker pueda usar el tier como tiebreaker.
- **Peso doctrinal** (`DoctrineWeight`): vinculante / consolidada /
  aislada. La doctrina reiterada (2+ sentencias del mismo órgano con
  mismo sentido sobre el mismo asunto) es una señal débil pero útil
  para que el LLM y el reranker prefieran criterios establecidos sobre
  pronunciamientos aislados.

El registry NO valida que el texto/contenido sea correcto: solo que el
identificador existe en el corpus. Cualquier afirmación sobre el
contenido debe seguir contrastándose contra los chunks del retriever.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from pathlib import Path
from typing import Iterable, Iterator

from ..models import (
    ConsultaDGT,
    Organo,
    OrganoTEA,
    ResolucionTEAC,
    Sentencia,
    TipoResolucion,
)


class JurisprudenceTier(IntEnum):
    """Jerarquía doctrinal. Menor número = mayor peso.

    Mezcla orden jurisdiccional (TC > TS > AN > TSJ > AP) con vía
    económico-administrativa porque, desde el punto de vista de la AEAT,
    una unificación de criterio TEAC (art. 242 LGT) vincula tan fuerte
    como una jurisprudencia consolidada del TS. Las DGT vinculantes
    quedan por debajo de TEAC ordinaria por escala doctrinal pero por
    encima de TEAR.

    Valores en decenas (TC=10, TS=20, AN=30, …) para dejar espacio a las
    figuras administrativas: `TEAC_UNIFICA=21` se sitúa entre TS y AN
    (más cerca de TS, equivalente en efecto vinculante AEAT) sin
    colisionar con otros miembros del enum — IntEnum colapsa miembros
    con el mismo valor en aliases, lo que romperia `tier.name` y la
    serialización en metadata.

    Si dos fuentes empatan en relevancia semántica, el reranker prefiere
    la de menor `int(tier)`.
    """

    TC = 10
    TS = 20
    TEAC_UNIFICA = 21
    AN = 30
    TEAC_EXTIENDE = 31
    TSJ = 40
    TEAC_ORDINARIA = 41
    DGT_VINCULANTE = 50
    TEAR = 60
    AP = 61


class DoctrineWeight(str, Enum):
    """Peso doctrinal heurístico para señalizar al LLM y al reranker.

    `BINDING`: norma de obligado cumplimiento para la AEAT (TC, TEAC
    unificación de criterio art. 242 LGT, DGT vinculante en supuesto
    idéntico art. 89 LGT).

    `CONSOLIDATED`: doctrina reiterada — 2+ sentencias del mismo órgano
    y sala con el mismo sentido sobre el mismo asunto. Heurística
    basada en metadatos; no sustituye análisis humano.

    `ISOLATED`: pronunciamiento único, todavía no consolidado.
    """

    BINDING = "binding"
    CONSOLIDATED = "consolidated"
    ISOLATED = "isolated"


@dataclass(frozen=True)
class SentenciaEntry:
    """Resumen indexable de una sentencia: lo justo para el guard + rerank."""

    ecli: str
    organo: Organo
    tribunal_codigo: str
    sala: str | None
    fecha: str
    fallo_sentido: str
    resumen: str | None
    asunto_key: str
    tier: JurisprudenceTier
    weight: DoctrineWeight


@dataclass(frozen=True)
class DgtEntry:
    """Resumen indexable de una consulta DGT vinculante."""

    numero: str
    fecha: str
    impuesto: str
    asunto: str
    tier: JurisprudenceTier
    weight: DoctrineWeight


@dataclass(frozen=True)
class TeacEntry:
    """Resumen indexable de una resolución TEAC/TEAR/TEAL."""

    numero: str
    organo: OrganoTEA
    fecha: str
    tipo: TipoResolucion
    impuesto: str
    asunto: str
    tier: JurisprudenceTier
    weight: DoctrineWeight


@dataclass
class JurisprudenceRegistry:
    """Índice auditable de jurisprudencia y doctrina administrativa.

    Tres mapas independientes por familia. Los identificadores se
    normalizan en el ingreso (`upper` para ECLI, canónicos para DGT y
    TEAC) y los lookups normalizan también para tolerar variaciones de
    capitalización y espacios.
    """

    sentencias: dict[str, SentenciaEntry] = field(default_factory=dict)
    dgt: dict[str, DgtEntry] = field(default_factory=dict)
    teac: dict[str, TeacEntry] = field(default_factory=dict)

    # ---------- Lookups ----------

    def knows_ecli(self, ecli: str) -> bool:
        return _norm_ecli(ecli) in self.sentencias

    def knows_dgt(self, numero: str) -> bool:
        return _norm_dgt(numero) in self.dgt

    def knows_teac(self, numero: str) -> bool:
        return _norm_teac(numero) in self.teac

    def get_sentencia(self, ecli: str) -> SentenciaEntry | None:
        return self.sentencias.get(_norm_ecli(ecli))

    def get_dgt(self, numero: str) -> DgtEntry | None:
        return self.dgt.get(_norm_dgt(numero))

    def get_teac(self, numero: str) -> TeacEntry | None:
        return self.teac.get(_norm_teac(numero))

    # ---------- Tamaño ----------

    @property
    def total(self) -> int:
        return len(self.sentencias) + len(self.dgt) + len(self.teac)

    def __bool__(self) -> bool:
        return self.total > 0

    # ---------- Constructores ----------

    @classmethod
    def from_items(
        cls,
        *,
        sentencias: Iterable[Sentencia] = (),
        dgt_consultas: Iterable[ConsultaDGT] = (),
        teac_resoluciones: Iterable[ResolucionTEAC] = (),
    ) -> "JurisprudenceRegistry":
        """Construye el registry a partir de objetos del modelo en memoria.

        Útil en tests (fixtures inline) y para reusar items recién
        ingeridos por el runner sin tocar disco.
        """
        sentencias_list = list(sentencias)
        weight_by_ecli = compute_sentencia_weights(sentencias_list)
        senten_map: dict[str, SentenciaEntry] = {}
        for s in sentencias_list:
            tier = tier_for_sentencia(s)
            weight = weight_by_ecli.get(s.ecli, DoctrineWeight.ISOLATED)
            senten_map[_norm_ecli(s.ecli)] = SentenciaEntry(
                ecli=s.ecli,
                organo=s.organo,
                tribunal_codigo=s.tribunal_codigo,
                sala=s.sala,
                fecha=s.fecha.isoformat(),
                fallo_sentido=s.fallo_sentido.value,
                resumen=s.resumen,
                asunto_key=_asunto_key(s.resumen),
                tier=tier,
                weight=weight,
            )

        dgt_map: dict[str, DgtEntry] = {}
        for c in dgt_consultas:
            dgt_map[_norm_dgt(c.numero)] = DgtEntry(
                numero=c.numero,
                fecha=c.fecha_salida.isoformat(),
                impuesto=c.impuesto.value,
                asunto=c.asunto,
                tier=JurisprudenceTier.DGT_VINCULANTE,
                # DGT vinculante: binding para la AEAT en supuesto idéntico
                # (art. 89 LGT). Marcarla siempre BINDING sería excesivo
                # — solo lo es en el supuesto del consultante. Mantenemos
                # ISOLATED por defecto y dejamos al LLM matizar.
                weight=DoctrineWeight.ISOLATED,
            )

        teac_resoluciones_list = list(teac_resoluciones)
        weight_by_teac = compute_teac_weights(teac_resoluciones_list)
        teac_map: dict[str, TeacEntry] = {}
        for r in teac_resoluciones_list:
            tier = tier_for_teac(r)
            weight = weight_by_teac.get(r.numero, DoctrineWeight.ISOLATED)
            teac_map[_norm_teac(r.numero)] = TeacEntry(
                numero=r.numero,
                organo=r.organo,
                fecha=r.fecha.isoformat(),
                tipo=r.tipo,
                impuesto=r.impuesto.value,
                asunto=r.asunto,
                tier=tier,
                weight=weight,
            )

        return cls(
            sentencias=senten_map,
            dgt=dgt_map,
            teac=teac_map,
        )

    @classmethod
    def from_disk(
        cls,
        *,
        jurisprudencia_dir: Path | None = None,
        dgt_dir: Path | None = None,
        teac_dir: Path | None = None,
    ) -> "JurisprudenceRegistry":
        """Carga el registry desde los directorios canónicos del corpus.

        Cualquier directorio omitido o inexistente se trata como vacío:
        el registry seguirá funcionando con las familias presentes. Eso
        permite enchufar la jurisprudencia incrementalmente (primero
        DGT, luego TEAC, luego CENDOJ) sin orquestar todo a la vez.
        """
        sentencias = list(_load_sentencias(jurisprudencia_dir))
        dgt = list(_load_dgt(dgt_dir))
        teac = list(_load_teac(teac_dir))
        return cls.from_items(
            sentencias=sentencias,
            dgt_consultas=dgt,
            teac_resoluciones=teac,
        )


# ---------- Carga desde disco ----------


def _load_sentencias(directory: Path | None) -> Iterator[Sentencia]:
    if directory is None or not directory.exists():
        return
    for path in sorted(directory.rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        yield Sentencia.from_dict(data)


def _load_dgt(directory: Path | None) -> Iterator[ConsultaDGT]:
    if directory is None or not directory.exists():
        return
    for path in sorted(directory.rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        yield ConsultaDGT.from_dict(data)


def _load_teac(directory: Path | None) -> Iterator[ResolucionTEAC]:
    if directory is None or not directory.exists():
        return
    for path in sorted(directory.rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        yield ResolucionTEAC.from_dict(data)


# ---------- Cálculo de tier ----------


def tier_for_sentencia(s: Sentencia) -> JurisprudenceTier:
    if s.organo == Organo.TC:
        return JurisprudenceTier.TC
    if s.organo == Organo.TS:
        return JurisprudenceTier.TS
    if s.organo == Organo.AN:
        return JurisprudenceTier.AN
    if s.organo == Organo.TSJ:
        return JurisprudenceTier.TSJ
    if s.organo == Organo.AP:
        return JurisprudenceTier.AP
    return JurisprudenceTier.TSJ  # fallback conservador


def tier_for_teac(r: ResolucionTEAC) -> JurisprudenceTier:
    if r.organo == OrganoTEA.TEAC:
        if r.tipo == TipoResolucion.UNIFICA_CRITERIO:
            return JurisprudenceTier.TEAC_UNIFICA
        if r.tipo == TipoResolucion.EXTIENDE_EFECTOS:
            return JurisprudenceTier.TEAC_EXTIENDE
        return JurisprudenceTier.TEAC_ORDINARIA
    # TEAR y TEAL: solo vinculan el caso resuelto.
    return JurisprudenceTier.TEAR


# ---------- Doctrina reiterada (heurística) ----------


def compute_sentencia_weights(
    sentencias: list[Sentencia],
) -> dict[str, DoctrineWeight]:
    """Asigna `DoctrineWeight` a cada sentencia por agrupación heurística.

    Regla: dos o más sentencias del MISMO órgano + MISMA sala +
    MISMO sentido + MISMO asunto (clave normalizada del resumen)
    → CONSOLIDATED. TC siempre BINDING. Resto → ISOLATED.

    La heurística es deliberadamente conservadora: prefiere infrarreportar
    "reiterada" (ISOLATED por defecto) antes que afirmar consolidación
    sin base. El LLM puede contradecirla si encuentra evidencia.
    """
    out: dict[str, DoctrineWeight] = {}
    # Agrupar por clave
    groups: dict[tuple[str, str, str, str], list[str]] = {}
    for s in sentencias:
        key = (
            s.organo.value,
            (s.sala or "").lower(),
            s.fallo_sentido.value,
            _asunto_key(s.resumen),
        )
        groups.setdefault(key, []).append(s.ecli)

    for s in sentencias:
        if s.organo == Organo.TC:
            out[s.ecli] = DoctrineWeight.BINDING
            continue
        key = (
            s.organo.value,
            (s.sala or "").lower(),
            s.fallo_sentido.value,
            _asunto_key(s.resumen),
        )
        if len(groups.get(key, [])) >= 2:
            out[s.ecli] = DoctrineWeight.CONSOLIDATED
        else:
            out[s.ecli] = DoctrineWeight.ISOLATED
    return out


def compute_teac_weights(
    resoluciones: list[ResolucionTEAC],
) -> dict[str, DoctrineWeight]:
    """Asigna peso doctrinal a resoluciones TEAC/TEAR.

    Reglas:
    - TEAC unifica criterio (art. 242 LGT) → BINDING (vinculante AEAT y TEAR).
    - TEAC extiende efectos (art. 244 LGT) → BINDING en supuestos análogos.
    - Resto TEAC ordinaria → ISOLATED (citables, no vinculantes).
    - TEAR/TEAL → ISOLATED siempre.
    - 2+ resoluciones TEAC ordinarias con misma materia/sentido → CONSOLIDATED.
    """
    out: dict[str, DoctrineWeight] = {}
    groups: dict[tuple[str, str, str], list[str]] = {}
    for r in resoluciones:
        key = (
            r.impuesto.value,
            r.sentido.value,
            _asunto_key(r.asunto),
        )
        groups.setdefault(key, []).append(r.numero)

    for r in resoluciones:
        if r.organo == OrganoTEA.TEAC and r.tipo in {
            TipoResolucion.UNIFICA_CRITERIO,
            TipoResolucion.EXTIENDE_EFECTOS,
        }:
            out[r.numero] = DoctrineWeight.BINDING
            continue
        key = (
            r.impuesto.value,
            r.sentido.value,
            _asunto_key(r.asunto),
        )
        if r.organo == OrganoTEA.TEAC and len(groups.get(key, [])) >= 2:
            out[r.numero] = DoctrineWeight.CONSOLIDATED
        else:
            out[r.numero] = DoctrineWeight.ISOLATED
    return out


# ---------- Normalización ----------


def _norm_ecli(raw: str) -> str:
    return raw.strip().upper()


def _norm_dgt(raw: str) -> str:
    """Normaliza un número DGT a `V<NNNN>-<YY>` (forma canónica 2 dígitos).

    Acepta entradas con padding y años a 4 dígitos. No valida formato
    estructuralmente (eso lo hace `rag.dgt.numero.parse_numero_consulta`);
    aquí solo normalizamos para que el lookup sea robusto a variaciones
    del LLM.
    """
    cleaned = raw.strip().upper().replace(" ", "")
    # V<dígitos>-<YY|YYYY>
    if cleaned.startswith("V") and "-" in cleaned:
        prefix, _, anyo = cleaned.partition("-")
        num_part = prefix[1:]
        try:
            num = int(num_part)
            anyo_int = int(anyo)
        except ValueError:
            return cleaned
        anyo_yy = anyo_int % 100 if anyo_int >= 100 else anyo_int
        return f"V{num:04d}-{anyo_yy:02d}"
    return cleaned


def _norm_teac(raw: str) -> str:
    """Normaliza un número TEAC a `DD/NNNNN/AAAA`.

    Acepta variantes `R.G. NNNN/YYYY`, `RG/NNNN/AAAA`, etc. Si el código
    de TEA no está presente, asume 00 (TEAC central). Resto del parsing
    se mantiene tolerante para no perder coincidencias por espacios o
    capitalización.
    """
    import re

    cleaned = raw.strip().upper()
    # Elimina prefijos R.G., RG, RG/
    cleaned = re.sub(r"^R\.?\s*G\.?[\.:/\s]+", "", cleaned)
    parts = [p.strip() for p in cleaned.split("/") if p.strip()]
    if len(parts) == 2:
        # NNNN/AAAA → 00/NNNNN/AAAA
        try:
            num = int(parts[0])
            anyo = int(parts[1])
        except ValueError:
            return cleaned
        anyo_full = anyo if anyo >= 100 else 2000 + anyo
        return f"00/{num:05d}/{anyo_full:04d}"
    if len(parts) >= 3:
        try:
            tea = int(parts[0])
            num = int(parts[1])
            anyo = int(parts[2])
        except ValueError:
            return cleaned
        anyo_full = anyo if anyo >= 100 else 2000 + anyo
        base = f"{tea:02d}/{num:05d}/{anyo_full:04d}"
        suffixes = parts[3:]
        if suffixes:
            base += "/" + "/".join(suffixes)
        return base
    return cleaned


def _asunto_key(text: str | None) -> str:
    """Clave normalizada para detectar sentencias del mismo asunto.

    Heurística: primeras 12 palabras alfanuméricas en minúsculas. No es
    semántica — un LLM podría agrupar mejor — pero suficiente para
    detectar duplicados evidentes ("dietas de manutención IRPF" vs
    "Dietas de manutención IRPF: trabajador desplazado").
    """
    if not text:
        return ""
    import re

    words = re.findall(r"[a-záéíóúñ0-9]+", text.lower())
    return " ".join(words[:12])


__all__ = [
    "DoctrineWeight",
    "DgtEntry",
    "JurisprudenceRegistry",
    "JurisprudenceTier",
    "SentenciaEntry",
    "TeacEntry",
    "compute_sentencia_weights",
    "compute_teac_weights",
    "tier_for_sentencia",
    "tier_for_teac",
]
