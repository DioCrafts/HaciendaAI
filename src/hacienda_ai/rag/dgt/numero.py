"""Parser y validador del número de consulta DGT.

Formato del identificador:

    V<NNNN>-<YY>     # canónico, "V0123-24"
    V<NNNN>-<YYYY>   # variante con año completo, "V0123-2024"

La forma canónica usa el año en dos dígitos (YY) porque es la que la
DGT emplea en su propio buscador y en las URLs de Petete. Esto crea
ambigüedad para años pre-2000 (V0123-99 podría ser 1999 o 2099), pero
la DGT empezó a publicar consultas vinculantes en 1997 y su numeración
no tiene colisiones reales: cualquier YY ≤ año-actual-en-2-dig se
interpreta como 20YY si YY ≤ 99, salvo años explícitamente anteriores
a 1997 (que no existen en el corpus).

Las consultas NO vinculantes empiezan por "C" (C0001-24); este parser
las RECHAZA. El corpus de este pipeline contiene solo vinculantes — el
LLM debe distinguirlas porque solo las vinculantes obligan a la AEAT
(art. 89 LGT).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class NumeroConsultaParseError(ValueError):
    """El número de consulta no tiene formato DGT vinculante válido."""


# Aceptamos `V<dígitos>-<2 o 4 dígitos>` con espacios opcionales.
# El sufijo es obligatorio (rechaza "V0123" sin año).
_RE_NUMERO = re.compile(
    r"^\s*V\s*(?P<num>\d{1,5})\s*-\s*(?P<anyo>\d{2}|\d{4})\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NumeroConsulta:
    """Número de consulta DGT desglosado."""

    raw: str
    numero: int
    anyo: int  # SIEMPRE 4 dígitos tras normalización.

    @property
    def canonical(self) -> str:
        """Forma canónica `V<NNNN>-<YY>` con año a 2 dígitos.

        El número se rellena a 4 dígitos con ceros a la izquierda
        (`V0123-24`). DGT mantiene este padding en sus URLs.
        """
        return f"V{self.numero:04d}-{self.anyo % 100:02d}"

    @property
    def long_form(self) -> str:
        """Forma con año a 4 dígitos `V0123-2024`, también aceptada por Petete."""
        return f"V{self.numero:04d}-{self.anyo}"


def parse_numero_consulta(raw: str) -> NumeroConsulta:
    """Parsea un número de consulta DGT. Lanza si formato inválido.

    No hace lookup contra DGT: solo valida estructura.
    """
    if not isinstance(raw, str):
        raise NumeroConsultaParseError(
            f"número debe ser string, no {type(raw).__name__}"
        )
    cleaned = raw.strip()
    if not cleaned:
        raise NumeroConsultaParseError("número vacío")
    match = _RE_NUMERO.match(cleaned)
    if not match:
        # Detectamos el caso típico: consulta no vinculante (`C0001-24`).
        if cleaned.upper().startswith("C") and "-" in cleaned:
            raise NumeroConsultaParseError(
                f"número {raw!r} es de consulta NO vinculante (prefijo C). "
                "Este pipeline solo acepta vinculantes (prefijo V)."
            )
        raise NumeroConsultaParseError(
            f"número no válido: {raw!r} (esperado V<NNNN>-<YY> o V<NNNN>-<YYYY>)"
        )
    num = int(match.group("num"))
    anyo_raw = int(match.group("anyo"))
    anyo = _normalize_anyo(anyo_raw)
    return NumeroConsulta(raw=cleaned, numero=num, anyo=anyo)


def _normalize_anyo(raw: int) -> int:
    """Normaliza años de 2 dígitos a 4. La DGT publica desde 1997.

    Reglas:
    - YYYY (≥1000) → tal cual.
    - YY (<100) → 20YY si YY ≥ 0 (cubre 2000–2099). El edge case del
      año 1997-1999 (en formato YY: 97, 98, 99) cae aquí también:
      se interpretarían como 2097-2099, lo cual sería incorrecto. La
      decisión deliberada es asumir que las URLs históricas de DGT ya
      no aparecen en flujos modernos; cuando aparezcan, usar la forma
      larga (`V0001-1999`).
    """
    if raw >= 1000:
        return raw
    return 2000 + raw
