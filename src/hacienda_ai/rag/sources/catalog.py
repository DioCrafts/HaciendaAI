"""Catálogo curado de fuentes oficiales para el copiloto fiscal.

Cada entrada apunta a la **versión consolidada o última publicación**
disponible en el BOE o en el portal del organismo. El URL es la página
HTML del documento (no el PDF) cuando hay una versión consolidada
mantenida por el BOE; en otros casos apunta al boletín autonómico.

Mantenido a mano. Si el URL ya no resuelve, abrir un PR con la nueva
referencia y `notes` explicando el cambio.

IMPORTANTE: este catálogo NO genera reglas automáticamente. Es el
material de lectura que un asesor fiscal o un colaborador del
proyecto utiliza para promover reglas de `pendiente_*` a `validada`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DocumentType = Literal[
    "ley",
    "ley_organica",
    "real_decreto",
    "real_decreto_legislativo",
    "decreto_legislativo",
    "manual",
    "consulta_dgt",
    "norma_foral",
]


@dataclass(frozen=True)
class OfficialSource:
    id: str
    title: str
    jurisdiction: str  # "estatal" o nombre de CCAA
    document_type: DocumentType
    url: str
    notes: str = ""


# Las URLs siguen el patrón de "consolidado vigente" del BOE
# (https://www.boe.es/buscar/act.php?id=...) cuando está disponible.
# Para CCAA con régimen común se prefiere la norma autonómica refundida.
CATALOG: tuple[OfficialSource, ...] = (
    # ---------- Estatal (régimen común) ----------
    OfficialSource(
        id="es_lirpf",
        title="Ley 35/2006, del IRPF (texto consolidado)",
        jurisdiction="estatal",
        document_type="ley",
        url="https://www.boe.es/buscar/act.php?id=BOE-A-2006-20764",
        notes="Norma matriz del IRPF estatal. Reforma frecuente; consultar siempre la versión consolidada.",
    ),
    OfficialSource(
        id="es_reglamento_irpf",
        title="Real Decreto 439/2007, Reglamento del IRPF (texto consolidado)",
        jurisdiction="estatal",
        document_type="real_decreto",
        url="https://www.boe.es/buscar/act.php?id=BOE-A-2007-6820",
        notes="Desarrollo reglamentario de la LIRPF: gastos deducibles, reducciones, retenciones, etc.",
    ),
    OfficialSource(
        id="es_ley_49_2002_mecenazgo",
        title="Ley 49/2002, régimen fiscal de entidades sin fines lucrativos y de los incentivos al mecenazgo",
        jurisdiction="estatal",
        document_type="ley",
        url="https://www.boe.es/buscar/act.php?id=BOE-A-2002-25039",
        notes="Marco de los donativos deducibles. Modificada por la Ley 7/2024 (porcentajes 80%/40%/45% y umbral 250 €).",
    ),
    OfficialSource(
        id="es_ley_7_2024_modif_mecenazgo",
        title="Ley 7/2024 — modificaciones del régimen de mecenazgo (Ley 49/2002)",
        jurisdiction="estatal",
        document_type="ley",
        url="https://www.boe.es/buscar/doc.php?id=BOE-A-2024-26521",
        notes="Eleva al 80 % el primer tramo y al 250 € el umbral; 40 %/45 % sobre exceso. Aplicable a partir de 2024.",
    ),
    OfficialSource(
        id="es_aeat_manual_practico_renta",
        title="AEAT — Manual práctico de Renta (índice de ediciones)",
        jurisdiction="estatal",
        document_type="manual",
        url="https://sede.agenciatributaria.gob.es/Sede/ayuda/manuales-videos-folletos/manuales-practicos.html",
        notes="Página índice. La edición vigente cambia cada año (publicada típicamente en marzo/abril).",
    ),
    OfficialSource(
        id="es_ley_6_2023_modif_irpf",
        title="Ley 6/2023 — ampliación del ámbito subjetivo de la deducción por maternidad",
        jurisdiction="estatal",
        document_type="ley",
        url="https://www.boe.es/buscar/doc.php?id=BOE-A-2023-7771",
        notes="Extiende la deducción por maternidad a mujeres con prestación por desempleo y otras situaciones.",
    ),
    # ---------- CCAA de régimen común ----------
    OfficialSource(
        id="auto_madrid_dlt",
        title="Decreto Legislativo 1/2010 — texto refundido tributos cedidos en la Comunidad de Madrid",
        jurisdiction="Madrid",
        document_type="decreto_legislativo",
        url="https://www.boe.es/buscar/act.php?id=BOE-A-2011-20262",
        notes="Recoge las deducciones autonómicas de Madrid en el IRPF. Modificado por leyes anuales.",
    ),
    OfficialSource(
        id="auto_cataluna_ley_19_2010",
        title="Ley 19/2010 — regulación del impuesto sobre sucesiones y donaciones y del IRPF (Cataluña)",
        jurisdiction="Cataluña",
        document_type="ley",
        url="https://dogc.gencat.cat/es/document-del-dogc/?documentId=549155",
        notes="Verificar la versión consolidada vigente en el portal jurídic de la Generalitat.",
    ),
    OfficialSource(
        id="auto_andalucia_dl_1_2018",
        title="Decreto Legislativo 1/2018 — texto refundido tributos cedidos (Andalucía)",
        jurisdiction="Andalucía",
        document_type="decreto_legislativo",
        url="https://www.juntadeandalucia.es/boja/2018/132/1",
        notes="Recopila las deducciones autonómicas andaluzas en el IRPF.",
    ),
    OfficialSource(
        id="auto_galicia_dl_1_2011",
        title="Decreto Legislativo 1/2011 — texto refundido tributos cedidos (Galicia)",
        jurisdiction="Galicia",
        document_type="decreto_legislativo",
        url="https://www.xunta.gal/dog/Publicados/2011/20111028/AnuncioC3F1-201011-0001_es.html",
        notes="Consultar la versión consolidada en el repertorio jurídico de la Xunta.",
    ),
    OfficialSource(
        id="auto_valenciana_ley_13_1997",
        title="Ley 13/1997 — tramo autonómico del IRPF y demás tributos cedidos (Comunitat Valenciana)",
        jurisdiction="Comunitat Valenciana",
        document_type="ley",
        url="https://dogv.gva.es/datos/1997/12/31/pdf/1997_15280.pdf",
        notes="Versión original publicada en el DOGV; modificada por leyes anuales de medidas fiscales.",
    ),
    OfficialSource(
        id="auto_aragon_dl_1_2005",
        title="Decreto Legislativo 1/2005 — texto refundido tributos cedidos (Aragón)",
        jurisdiction="Aragón",
        document_type="decreto_legislativo",
        url="https://www.boa.aragon.es/cgi-bin/EBOA/BRSCGI?CMD=VEROBJ&MLKOB=92858410404",
        notes="Texto refundido aragonés; verificar consolidación en el BOA.",
    ),
    OfficialSource(
        id="auto_castillayleon_dl_1_2013",
        title="Decreto Legislativo 1/2013 — texto refundido tributos propios y cedidos (Castilla y León)",
        jurisdiction="Castilla y León",
        document_type="decreto_legislativo",
        url="https://bocyl.jcyl.es/boletines/2013/09/27/pdf/BOCYL-D-27092013-1.pdf",
        notes="PDF original en BOCYL; consultar la JCyL para consolidaciones posteriores.",
    ),
    OfficialSource(
        id="auto_baleares_dl_1_2014",
        title="Decreto Legislativo 1/2014 — texto refundido tributos cedidos (Illes Balears)",
        jurisdiction="Illes Balears",
        document_type="decreto_legislativo",
        url="https://www.caib.es/eboibfront/es/2014/8377/542691/decreto-legislativo-1-2014-de-6-de-junio-por-el-cu",
        notes="BOIB: texto refundido balear de tributos cedidos.",
    ),
    # ---------- Régimen foral ----------
    OfficialSource(
        id="foral_navarra_lf_2_2018",
        title="Ley Foral 2/2018 — modificaciones tributarias (Navarra). IRPF foral",
        jurisdiction="Navarra",
        document_type="norma_foral",
        url="https://bon.navarra.es/es/anuncio/-/texto/2018/85/0",
        notes="Régimen foral propio: el IRPF de Navarra NO se rige por la Ley 35/2006. Consultar la web del Gobierno de Navarra para la normativa vigente del ejercicio.",
    ),
    OfficialSource(
        id="foral_bizkaia_nf_13_2013",
        title="Norma Foral 13/2013 del IRPF (Bizkaia)",
        jurisdiction="País Vasco · Bizkaia",
        document_type="norma_foral",
        url="https://www.bizkaia.eus/lehendakaritza/Bao_bob/2013/12/20131213a236.pdf",
        notes="Régimen foral. Existen normas equivalentes para Álava y Gipuzkoa; añadir cuando se cubran.",
    ),
    # ---------- Consultas vinculantes de la DGT (entradas temáticas) ----------
    # IMPORTANTE: cada entrada apunta a una BÚSQUEDA temática en el portal de
    # consultas vinculantes de la DGT (no a una consulta concreta). El motivo es
    # honestidad: fabricar V-numbers específicos sin verificación entrega
    # referencias inválidas. Para añadir una consulta concreta, abrir un PR con
    # OfficialSource(id="dgt_v<N>_<YY>_<slug>", reference exacta, fecha y URL
    # directa al PDF/HTML de esa consulta) y reemplazar la entrada genérica.
    # Portal DGT (Petete): https://petete.tributos.hacienda.gob.es/consultas/
    OfficialSource(
        id="dgt_busqueda_planes_pensiones",
        title="DGT — búsqueda de consultas sobre planes de pensiones (art. 51-52 LIRPF)",
        jurisdiction="estatal",
        document_type="consulta_dgt",
        url="https://petete.tributos.hacienda.gob.es/consultas/?num_consulta=&fecha_consulta_desde=&fecha_consulta_hasta=&general=plan+de+pensiones",
        notes="Búsqueda en el portal Petete de DGT. Útil para localizar criterios sobre límites individuales, aportaciones del cónyuge, planes de empleo y rescates.",
    ),
    OfficialSource(
        id="dgt_busqueda_donativos_mecenazgo",
        title="DGT — búsqueda sobre donativos y mecenazgo (Ley 49/2002 art. 19)",
        jurisdiction="estatal",
        document_type="consulta_dgt",
        url="https://petete.tributos.hacienda.gob.es/consultas/?num_consulta=&general=Ley+49%2F2002+donativo",
        notes="Localiza consultas sobre certificación de donativos, donaciones en especie, donativo recurrente y entidades beneficiarias del art. 16.",
    ),
    OfficialSource(
        id="dgt_busqueda_maternidad",
        title="DGT — búsqueda sobre deducción por maternidad (art. 81 LIRPF)",
        jurisdiction="estatal",
        document_type="consulta_dgt",
        url="https://petete.tributos.hacienda.gob.es/consultas/?num_consulta=&general=deducci%C3%B3n+por+maternidad+art%C3%ADculo+81",
        notes="Especial atención a la ampliación de Ley 6/2023 (mujeres con prestación por desempleo y otras situaciones equiparadas).",
    ),
    OfficialSource(
        id="dgt_busqueda_familia_numerosa",
        title="DGT — búsqueda sobre deducciones de familia numerosa y discapacidad (art. 81 bis LIRPF)",
        jurisdiction="estatal",
        document_type="consulta_dgt",
        url="https://petete.tributos.hacienda.gob.es/consultas/?num_consulta=&general=art%C3%ADculo+81+bis+familia+numerosa",
        notes="Cubre el prorrateo entre varios contribuyentes, el incremento por hijo adicional y la cesión del derecho a otro contribuyente.",
    ),
    OfficialSource(
        id="dgt_busqueda_cuotas_sindicales",
        title="DGT — búsqueda sobre cuotas sindicales y colegios profesionales (art. 19.2 LIRPF)",
        jurisdiction="estatal",
        document_type="consulta_dgt",
        url="https://petete.tributos.hacienda.gob.es/consultas/?num_consulta=&general=cuotas+sindicales+colegios+profesionales",
        notes="Criterios sobre el carácter obligatorio de la colegiación para deducir las cuotas (art. 19.2.d).",
    ),
    OfficialSource(
        id="dgt_busqueda_alquiler_vivienda",
        title="DGT — búsqueda sobre alquiler de vivienda habitual (art. 68.7 LIRPF y autonómicas)",
        jurisdiction="estatal",
        document_type="consulta_dgt",
        url="https://petete.tributos.hacienda.gob.es/consultas/?num_consulta=&general=alquiler+vivienda+habitual+deducci%C3%B3n",
        notes="Régimen transitorio del art. 68.7 LIRPF (contratos anteriores a 2015) y deducciones autonómicas para jóvenes.",
    ),
    OfficialSource(
        id="dgt_busqueda_ceuta_melilla",
        title="DGT — búsqueda sobre bonificación de Ceuta y Melilla (art. 68.4 LIRPF)",
        jurisdiction="estatal",
        document_type="consulta_dgt",
        url="https://petete.tributos.hacienda.gob.es/consultas/?num_consulta=&general=bonificaci%C3%B3n+Ceuta+Melilla+art%C3%ADculo+68",
        notes="Criterio para determinar la cuota correspondiente a rentas obtenidas en Ceuta/Melilla y la antigüedad de residencia (≥ 3 años).",
    ),
    OfficialSource(
        id="dgt_busqueda_teletrabajo_gastos_trabajo",
        title="DGT — búsqueda sobre teletrabajo, gastos de difícil justificación y rendimientos del trabajo",
        jurisdiction="estatal",
        document_type="consulta_dgt",
        url="https://petete.tributos.hacienda.gob.es/consultas/?num_consulta=&general=teletrabajo+gastos+rendimientos+trabajo",
        notes="Especial atención a los gastos del art. 19.2.f LIRPF (otros gastos genérico 2.000 €) y al criterio sobre teletrabajo desde 2020.",
    ),
    OfficialSource(
        id="dgt_busqueda_rendimientos_capital_inmobiliario",
        title="DGT — búsqueda sobre rendimientos del capital inmobiliario (art. 22-24 LIRPF)",
        jurisdiction="estatal",
        document_type="consulta_dgt",
        url="https://petete.tributos.hacienda.gob.es/consultas/?num_consulta=&general=rendimientos+capital+inmobiliario+art%C3%ADculo+23",
        notes="Gastos deducibles del arrendamiento, reducción del 60 % por vivienda habitual del arrendatario (art. 23.2) y prorrateo de gastos.",
    ),
    OfficialSource(
        id="dgt_busqueda_ganancias_reinversion_vivienda",
        title="DGT — búsqueda sobre exención por reinversión en vivienda habitual (art. 38 LIRPF)",
        jurisdiction="estatal",
        document_type="consulta_dgt",
        url="https://petete.tributos.hacienda.gob.es/consultas/?num_consulta=&general=reinversi%C3%B3n+vivienda+habitual+art%C3%ADculo+38",
        notes="Plazos, requisitos de habitabilidad efectiva, exención por mayores de 65 años (art. 33.4.b).",
    ),
)
