"""Controles básicos para rechazar solicitudes contrarias a la legalidad."""

from __future__ import annotations

ILLEGAL_PATTERNS = (
    "facturas falsas",
    "factura falsa",
    "inventar gastos",
    "ocultar ingresos",
    "no declarar ingresos",
    "falsear residencia",
    "simular residencia",
    "manipular fechas",
)

LEGAL_REDIRECT = (
    "No puedo ayudar a ocultar ingresos, inventar gastos, falsear datos o simular operaciones. "
    "Sí puedo ayudarte a revisar deducciones, reducciones y documentación necesaria dentro de la legalidad."
)


def screen_user_request(text: str) -> tuple[bool, str | None]:
    normalized = " ".join(text.lower().split())
    if any(pattern in normalized for pattern in ILLEGAL_PATTERNS):
        return False, LEGAL_REDIRECT
    return True, None
