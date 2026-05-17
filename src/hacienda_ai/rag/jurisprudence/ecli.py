"""Parser y validador de identificadores ECLI españoles.

ECLI (European Case Law Identifier) es el estándar europeo para citar
jurisprudencia de forma estable. Formato canónico:

    ECLI:<código_país>:<código_órgano>:<año>:<id_interno>

Para sentencias españolas:
- País: siempre `ES`.
- Código órgano: `TS` (Tribunal Supremo), `AN` (Audiencia Nacional),
  `TSJ<provincia>` (ej. `TSJM` para TSJ Madrid, `TSJAND` para Andalucía),
  `AP<provincia>` (ej. `APM`, `APB`), `TC` (Tribunal Constitucional).
- Año: 4 dígitos.
- Id interno: alfanumérico, asignado por el sistema CENDOJ.

Algunas variantes históricas añaden un sufijo tras el id (`.S2` para
sala 2ª, etc.) pero el formato base SIEMPRE se respeta.

Este módulo:
- Valida el formato del ECLI.
- Extrae los campos en una estructura tipada.
- Mapea `tribunal_codigo` → `Organo` genérico (TS/AN/TSJ/AP/TC) para
  razonamiento jerárquico sobre la sentencia.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...models import Organo


class EcliParseError(ValueError):
    """El identificador no tiene formato ECLI español válido."""


# Patrón base del ECLI. Aceptamos sufijos opcionales tras el id interno
# (p.ej. `.S2`, `:S2`) que algunas fuentes añaden — los conservamos en el
# campo `raw` pero no los incluimos en `id_interno`.
_RE_ECLI = re.compile(
    r"^ECLI:ES:"
    r"(?P<tribunal>[A-Z]+[A-Z0-9]*):"
    r"(?P<anyo>\d{4}):"
    r"(?P<id>[A-Z0-9.]+?)"
    r"(?P<suffix>[:.][A-Z0-9]+)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ECLI:
    """Identificador ECLI desglosado."""

    raw: str
    tribunal_codigo: str
    anyo: int
    id_interno: str

    @property
    def canonical(self) -> str:
        """Forma canónica `ECLI:ES:<tribunal>:<año>:<id>` sin sufijos."""
        return f"ECLI:ES:{self.tribunal_codigo}:{self.anyo}:{self.id_interno}"


def parse_ecli(raw: str) -> ECLI:
    """Parsea un identificador ECLI español. Lanza si formato inválido.

    No hace lookup contra CENDOJ: solo valida estructura.
    """
    if not isinstance(raw, str):
        raise EcliParseError(f"ECLI debe ser string, no {type(raw).__name__}")
    cleaned = raw.strip()
    if not cleaned:
        raise EcliParseError("ECLI vacío")
    match = _RE_ECLI.match(cleaned)
    if not match:
        raise EcliParseError(
            f"ECLI no válido: {raw!r} (esperado ECLI:ES:<tribunal>:<año>:<id>)"
        )
    return ECLI(
        raw=cleaned,
        tribunal_codigo=match.group("tribunal").upper(),
        anyo=int(match.group("anyo")),
        id_interno=match.group("id").upper(),
    )


# Mapping de prefijos de `tribunal_codigo` al `Organo` genérico. El TS
# es un único código; TSJs y APs llevan sufijo provincial variable.
_TRIBUNAL_PREFIX_TO_ORGANO: tuple[tuple[str, Organo], ...] = (
    ("TC", Organo.TC),
    ("TSJ", Organo.TSJ),
    ("TS", Organo.TS),
    ("AN", Organo.AN),
    ("AP", Organo.AP),
)


def organo_from_tribunal_codigo(tribunal_codigo: str) -> Organo:
    """Devuelve el `Organo` genérico al que pertenece un código de tribunal.

    El orden importa: TC (Constitucional) y TSJ (autonómicos) comparten
    raíz "T" con TS, así que evaluamos primero los prefijos más
    específicos.

    Lanza `EcliParseError` si el código no encaja con ningún órgano
    conocido (ej. típo en el ECLI).
    """
    code = tribunal_codigo.strip().upper()
    if not code:
        raise EcliParseError("tribunal_codigo vacío")
    for prefix, organo in _TRIBUNAL_PREFIX_TO_ORGANO:
        if code == prefix or code.startswith(prefix):
            # Salvaguarda: "TS" no debe capturar "TSJ"; lo evita el orden
            # de la tupla (TSJ va antes). Confirmamos explícito.
            if prefix == "TS" and code.startswith("TSJ"):
                continue
            if prefix == "TC" and len(code) > 2 and not code[2:].isdigit():
                # "TC" solo, no "TCXYZ". Constitucional es siempre "TC".
                continue
            if prefix == "AN" and code != "AN":
                # AN es un único tribunal; no hay "AN<provincia>".
                continue
            return organo
    raise EcliParseError(
        f"tribunal_codigo no reconocido: {tribunal_codigo!r}"
    )
