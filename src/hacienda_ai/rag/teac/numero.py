"""Parser y validador del número de reclamación TEAC/TEAR.

Formato canónico utilizado por TEAC:

    <DD>/<NNNNN>/<AAAA>             # base: "00/12345/2023"
    <DD>/<NNNNN>/<AAAA>/<SS>/<MM>   # con sufijos sección/incidente

`DD` es el código de TEA (00 = central; códigos 01-52 corresponden a
TEAR/TEAL provinciales). `NNNNN` es el número de reclamación. `AAAA` es
el año de entrada.

Variantes que aceptamos como entrada y normalizamos al canónico:

    R.G. 12345/2023        → 00/12345/2023
    R.G.: 12345/2023       → 00/12345/2023
    RG/12345/2023          → 00/12345/2023
    12345/2023             → 00/12345/2023  (asumimos TEAC si no se especifica)
    28/00345/2024          → 28/00345/2024  (TEAR/TEAL provincial; código 28 = Madrid)

`NNNNN` se rellena con ceros a la izquierda al canonizar (00345 en vez
de 345).

NOTA: el código provincial (28 Madrid, 08 Barcelona, etc.) sigue la
nomenclatura ISO de provincias, pero el TEAC no la documenta
formalmente. La detección órgano (TEAC/TEAR/TEAL) la hace `runner.py`
combinando este código con el contenido del HTML.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class NumeroReclamacionParseError(ValueError):
    """Formato del número de reclamación inválido."""


# Forma con código de TEA explícito: "00/12345/2023" con sufijos opcionales.
_RE_NUMERO_COMPLETO = re.compile(
    r"^\s*"
    r"(?P<tea>\d{1,2})\s*/\s*"
    r"(?P<num>\d{1,7})\s*/\s*"
    r"(?P<anyo>\d{2,4})"
    r"(?:\s*/\s*(?P<seccion>\d{1,3}))?"
    r"(?:\s*/\s*(?P<sub>\d{1,3}))?"
    r"\s*$"
)

# Forma corta R.G.: "R.G. 12345/2023" o "RG/12345/2023" o solo "12345/2023".
_RE_NUMERO_CORTO = re.compile(
    r"^\s*(?:R\.?\s*G\.?[\.:/\s]+)?(?P<num>\d{1,7})\s*/\s*(?P<anyo>\d{2,4})\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NumeroReclamacion:
    """Número de reclamación TEAC/TEAR desglosado."""

    raw: str
    codigo_tea: int  # 0 = TEAC central; 1-52 = TEAR/TEAL provincial.
    numero: int
    anyo: int  # 4 dígitos tras normalización.
    seccion: int | None
    subexpediente: int | None

    @property
    def canonical(self) -> str:
        """Forma canónica `DD/NNNNN/AAAA` con sufijos si existen.

        `DD` es el código TEA con padding a 2 dígitos. `NNNNN` se rellena
        a 5 dígitos. Año a 4 dígitos.
        """
        base = (
            f"{self.codigo_tea:02d}/"
            f"{self.numero:05d}/"
            f"{self.anyo:04d}"
        )
        if self.seccion is not None:
            base += f"/{self.seccion:02d}"
            if self.subexpediente is not None:
                base += f"/{self.subexpediente:02d}"
        return base

    @property
    def is_teac_central(self) -> bool:
        """`True` si el código de TEA es 0 (TEAC central)."""
        return self.codigo_tea == 0


def parse_numero_reclamacion(raw: str) -> NumeroReclamacion:
    """Parsea un número de reclamación TEAC/TEAR. Lanza si formato inválido.

    Acepta varias formas (ver docstring del módulo) y normaliza a la
    canónica al construir `NumeroReclamacion.canonical`.
    """
    if not isinstance(raw, str):
        raise NumeroReclamacionParseError(
            f"número debe ser string, no {type(raw).__name__}"
        )
    cleaned = raw.strip()
    if not cleaned:
        raise NumeroReclamacionParseError("número vacío")

    match = _RE_NUMERO_COMPLETO.match(cleaned)
    if match:
        codigo = int(match.group("tea"))
        num = int(match.group("num"))
        anyo = _normalize_anyo(int(match.group("anyo")))
        seccion = (
            int(match.group("seccion"))
            if match.group("seccion") is not None
            else None
        )
        sub = (
            int(match.group("sub"))
            if match.group("sub") is not None
            else None
        )
        return NumeroReclamacion(
            raw=cleaned,
            codigo_tea=codigo,
            numero=num,
            anyo=anyo,
            seccion=seccion,
            subexpediente=sub,
        )

    match = _RE_NUMERO_CORTO.match(cleaned)
    if match:
        # Forma corta R.G.: asumimos TEAC central (código 0).
        num = int(match.group("num"))
        anyo = _normalize_anyo(int(match.group("anyo")))
        return NumeroReclamacion(
            raw=cleaned,
            codigo_tea=0,
            numero=num,
            anyo=anyo,
            seccion=None,
            subexpediente=None,
        )

    raise NumeroReclamacionParseError(
        f"número no válido: {raw!r} (esperado DD/NNNNN/AAAA o R.G. NNNNN/AAAA)"
    )


def _normalize_anyo(raw: int) -> int:
    """Normaliza años de 2 dígitos a 4. TEAC publica desde los 80 (corto plazo).

    Por simplicidad asumimos que YY < 100 → 20YY. Para años 1980-1999
    debe usarse la forma con YYYY explícito.
    """
    if raw >= 1000:
        return raw
    return 2000 + raw
