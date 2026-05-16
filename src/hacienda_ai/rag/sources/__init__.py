"""Conectores hacia fuentes oficiales (BOE estatal + boletines autonómicos).

El conector BOE estatal con verificación SHA-256 de pinpoint por artículo
vive en `scripts/verify_seed.py` y la GitHub Action lo invoca diariamente.
Este paquete reúne los conectores nuevos a medida que se incorporan; hoy
solo `regional` (chequeo de URLs de boletines autonómicos), suficiente
para detectar enlaces rotos sin pretender verificar texto íntegro.
"""

from .regional import (
    REGIONAL_BULLETIN_PREFIXES,
    URLCheckResult,
    check_regional_urls,
)

__all__ = [
    "REGIONAL_BULLETIN_PREFIXES",
    "URLCheckResult",
    "check_regional_urls",
]
