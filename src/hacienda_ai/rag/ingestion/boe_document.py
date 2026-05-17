"""Hash normalizado del XML de un documento publicado en BOE.

A diferencia de `scripts/verify_seed.py`, que hashea el cuerpo de un
`<bloque>` específico del texto **consolidado** para verificar pinpoints,
aquí hasheamos el documento **publicado** entero (todo el `<texto>`) para
producir una huella estable de la disposición tal como apareció en el
boletín.

Ese hash:

- Va a `VersionNorma.content_hash` cuando creamos una entrada nueva en
  el corpus. Sirve para detectar (improbable pero posible) cambios en el
  XML publicado del BOE, que documentaríamos como incidencia.
- Es estable frente a reordenaciones triviales de espacios y comentarios
  XML.

Normalización:
1. Extraer el contenido del elemento `<texto>` (o `<documento>` si no hay
   `<texto>` directo; varía entre tipos de disposición).
2. Eliminar comentarios y declaraciones de procesamiento.
3. Eliminar tags HTML/XML internos (`<p>`, `<a>`, etc.), conservando el
   texto plano.
4. Colapsar runs de whitespace a espacio único.
5. Trim.
6. SHA-256 hex.

El hash de un documento muy corto (resoluciones, anuncios) puede ser
poco discriminante, pero como solo se aplica a items que han pasado el
filtro fiscal y se anclan a `boe_id`, no hay riesgo de colisión funcional.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET

_RE_ANY_TAG = re.compile(r"<[^>]+>")
_RE_WHITESPACE = re.compile(r"\s+")


class DocumentHashError(ValueError):
    """No se pudo extraer cuerpo hasheable del documento."""


def extract_body(xml: str) -> str:
    """Extrae el cuerpo textual normativo del XML publicado.

    El XML del BOE para un documento tiene esta estructura:

        <documento>
          <metadatos>...</metadatos>
          <analisis>
            <referencias>
              <anteriores>
                <anterior><texto>...</texto></anterior>   ← NO es el cuerpo
              </anteriores>
            </referencias>
          </analisis>
          <texto>...</texto>                              ← ESTE es el cuerpo
        </documento>

    Hay `<texto>` anidados dentro de `<analisis>` que NO son el articulado
    de la disposición sino glosas editoriales sobre referencias a otras
    normas. Por eso usamos un parser XML real (no regex) para tomar
    exclusivamente el `<texto>` hijo directo de `<documento>`.

    Si el documento no tiene `<texto>` top-level (raro: algunos anuncios),
    serializamos el `<documento>` entero como fallback.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise DocumentHashError(f"XML del documento malformado: {exc}") from exc

    # Si `root` ya es `<documento>`, tomamos su hijo directo `<texto>`. Si
    # `root` es otro envoltorio (p.ej. una respuesta de la API que envuelve
    # el documento), buscamos primero `<documento>`.
    documento = root if root.tag == "documento" else root.find(".//documento")
    if documento is None:
        raise DocumentHashError("XML sin elemento <documento>")

    texto = documento.find("texto")  # solo hijo DIRECTO, no descendientes.
    target = texto if texto is not None else documento

    # Serializamos el elemento elegido a string para que `normalize_body`
    # le aplique la limpieza estándar. Usamos `itertext` para concatenar
    # todo el texto descendiente preservando orden.
    parts = list(target.itertext())
    if not parts:
        raise DocumentHashError("cuerpo del documento vacío")
    return "".join(parts)


def normalize_body(body_xml: str) -> str:
    """Convierte cuerpo XML a texto plano normalizado para hashing.

    Elimina todos los tags conservando el texto entre ellos, decodifica
    entidades comunes (&amp;, &lt;, &gt;, &nbsp;, &#160;) a sus
    equivalentes, y colapsa whitespace.
    """
    text = _RE_ANY_TAG.sub(" ", body_xml)
    # Decodificación mínima de entidades. No usamos `html.unescape` para
    # evitar variabilidad entre versiones de stdlib; estas 6 cubren el
    # ~99% de los casos en BOE.
    for entity, replacement in (
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&apos;", "'"),
        ("&nbsp;", " "),
        ("&#160;", " "),
    ):
        text = text.replace(entity, replacement)
    return _RE_WHITESPACE.sub(" ", text).strip()


def hash_document(xml: str) -> tuple[str, str]:
    """Devuelve `(sha256_hex, texto_normalizado)` del documento.

    Expone el texto normalizado además del hash para que el caller pueda
    inspeccionarlo en logs/debug y, eventualmente, persistirlo como
    snapshot del documento (no se hace por defecto para no inflar el
    repo).
    """
    body = extract_body(xml)
    normalized = normalize_body(body)
    if not normalized:
        raise DocumentHashError("cuerpo del documento vacío tras normalización")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest, normalized
