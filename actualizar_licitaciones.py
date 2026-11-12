#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Actualiza los datos del panel de licitaciones activas de Mercado Público
para la Región de O'Higgins (CChC O'Higgins, Estudios).

Qué hace, paso a paso:
  1. Pide a la API de Mercado Público el listado de licitaciones ACTIVAS del país.
  2. Para las que aún no conoce, consulta el detalle (organismo, unidad compradora,
     región, comuna, fechas, rubros, monto). El resultado queda en data/cache.json,
     así cada día solo se consultan las licitaciones nuevas (cuida el cupo diario).
  3. Se queda con las de la Región de O'Higgins y genera docs/data.json,
     que es lo que lee el dashboard (docs/index.html).

El ticket de la API se lee SOLO desde la variable de entorno MERCADO_PUBLICO_TICKET
(en GitHub: Settings > Secrets and variables > Actions). Nunca se escribe en archivos
ni en los registros de ejecución.

Códigos de salida: 0 = ok (incluye actualización parcial), 1 = la API no entregó
datos (no se modifica nada), 2 = falta el ticket o configuración inválida,
3 = la API rechazó el ticket.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# --------------------------------------------------------------------------- #
# Configuración (todo se puede ajustar con variables de entorno)
# --------------------------------------------------------------------------- #
RAIZ = Path(__file__).resolve().parent
RUTA_CACHE = RAIZ / "data" / "cache.json"
RUTA_SALIDA = RAIZ / "docs" / "data.json"
TZ = ZoneInfo("America/Santiago")

BASE_URL = os.environ.get("MP_BASE_URL", "https://api.mercadopublico.cl").rstrip("/")
ENDPOINT = BASE_URL + "/servicios/v1/publico/licitaciones.json"
TICKET = os.environ.get("MERCADO_PUBLICO_TICKET", "").strip()

# Pausa entre consultas de detalle. La API rechaza peticiones simultáneas,
# por eso se consulta de a una.
PAUSA_SEG = float(os.environ.get("MP_PAUSA_SEG", "0.4"))
# Tope de consultas de detalle por ejecución (protege el cupo diario de la API).
MAX_DETALLES = int(os.environ.get("MP_MAX_DETALLES", "6000"))
# Tiempo máximo (minutos) dedicado a consultar detalles. GitHub corta el proceso a los 240 min
# sin guardar nada, así que se corta antes, se guarda lo avanzado y se continúa en la próxima ejecución.
MAX_MINUTOS = float(os.environ.get("MP_MAX_MINUTOS", "190"))
# Cada cuántos días se vuelve a consultar el detalle de las licitaciones de la región
# (para captar cambios de descripción o monto).
REFRESCO_DIAS = int(os.environ.get("MP_REFRESCO_DIAS", "7"))
# Plantilla del link directo a la ficha. Si Mercado Público cambia el formato,
# basta ajustar esta línea. Alternativa:
# https://www.mercadopublico.cl/fichaLicitacion.html?idLicitacion={codigo}
URL_FICHA = os.environ.get(
    "MP_URL_FICHA",
    "https://www.mercadopublico.cl/Procurement/Modules/RFB/DetailsAcquisition.aspx?idlicitacion={codigo}",
)
DEMO = os.environ.get("MP_DEMO") == "1"  # solo para pruebas con datos simulados

# Comunas de la Región del Libertador General Bernardo O'Higgins (33).
COMUNAS_OHIGGINS = {
    # Provincia de Cachapoal
    "rancagua", "codegua", "coinco", "coltauco", "donihue", "graneros", "las cabras",
    "machali", "malloa", "mostazal", "olivar", "peumo", "pichidegua",
    "quinta de tilcoco", "rengo", "requinoa", "san vicente",
    # Provincia Cardenal Caro
    "pichilemu", "la estrella", "litueche", "marchihue", "navidad", "paredones",
    # Provincia de Colchagua
    "san fernando", "chepica", "chimbarongo", "lolol", "nancagua", "palmilla",
    "peralillo", "placilla", "pumanque", "santa cruz",
}

TIPOS = {  # referencial; los códigos no listados se muestran tal cual
    "L1": "Pública menor a 100 UTM",
    "LE": "Pública 100 a 1.000 UTM",
    "LP": "Pública 1.000 a 2.000 UTM",
    "LQ": "Pública 2.000 a 5.000 UTM",
    "LR": "Pública desde 5.000 UTM",
    "LS": "Servicios personales especializados",
}

# Rubros afines a la construcción (referencial). Tres reglas que se suman:
#  1) código de clasificación UNSPSC (solo familias de obra, ingeniería civil, terrenos y edificios),
#  2) palabras específicas en el nombre o rubro (no palabras sueltas como "reposición" o "infraestructura"),
#  3) organismos de obras públicas (MOP), salvo que el nombre indique otra cosa (aseo, vehículos, insumos...).
AFIN_PREFIJOS = ("30", "721015", "721016", "721033", "7211", "7212", "7214", "7215", "8110",
                 "95", "3910", "4010", "4014", "4017")

_OBJETOS_OBRA = (
    r"calles?|pasajes?|avenidas?|plazas?|parques?|canchas?|multicanchas?|sedes?|escuelas?|liceos?|colegios?|"
    r"jardin(?:es)? infantil(?:es)?|salas? cuna|cesfam|cecosf|sapu|hospital(?:es)?|consultorios?|postas?|"
    r"edificios?|centros? (?:comunitario|de salud|deportivo|cultural|vecinal|de rehabilitacion)|gimnasios?|"
    r"estadios?|complejos?|techumbres?|cubiertas?|cierres? perimetral(?:es)?|redes?|pavimentos?|"
    r"espacios? (?:publicos?|deportivos?|comunitarios?)|clubes?|bibliotecas?|bodegas?|cuarteles?|casinos?|"
    r"comisarias?|alumbrado|iluminacion|luminarias|semaforos?|infraestructura|recintos?|cementerios?|"
    r"terminal(?:es)?|paraderos?|sistemas? de (?:agua|alcantarillado|riego)|banos?|camarines|"
    r"servicios? higienicos?|caminos?|rutas?|puentes?|veredas?|aceras?|calzadas?|viviendas?|aulas?|"
    r"cocinas?|comedor(?:es)?|areas? verdes?"
)
_OBJETOS_MANT = (
    r"calles?|calzadas?|veredas?|aceras?|caminos?|puentes?|techumbres?|cubiertas?|cierres? perimetral(?:es)?|"
    r"pavimentos?|pasajes?|edificios?|sedes?|escuelas?|liceos?|colegios?|cesfam|recintos?|infraestructura|"
    r"alumbrado publico|redes? de (?:agua|alcantarillado|alumbrado)|semaforos?"
)
# Palabras que bastan por sí solas.
AFIN_FUERTE = re.compile(
    r"construcc|edificac|pavimenta|urbaniz|alcantarillado|agua potable rural|\bapr\b|"
    r"\bobras? (?:civil|civiles|vial(?:es)?|menores|mayores|complementarias?|de (?:arte|edificacion|urbanizacion|"
    r"construccion|mejoramiento|conservacion))|obra gruesa|movimientos? de tierras?|excavac|hormigon|asfalto|"
    r"bacheo|demarcacion|travesias?|seguridad vial|carretera|\bruta\b|"
    r"caminos? (?:basico|vecinal|rural|publico)|conservacion (?:global|vial)|\bpuentes?\b|pasarela|ciclovia|"
    r"\bveredas?\b|\baceras?\b|\bsoleras?\b|\bcalzadas?\b|colector|saneamiento sanitario|"
    r"embalse|canal (?:de riego|matriz)|defensas? fluvial(?:es)?|muro de contencion|"
    r"preinversion|\bpreinv\b|estudio de (?:ingenieria|prefactibilidad|factibilidad|mecanica de suelos)|"
    r"diseno de (?:ingenieria|arquitectura|obras?)|proyecto de (?:arquitectura|ingenieria|construccion)|"
    r"instalacion de (?:tuberias?|canerias?|redes? de (?:agua|alcantarillado|gas|electricas?)|"
    r"sistemas? de (?:agua|alcantarillado|riego))|"
    r"inspeccion tecnica|\bito\b|complejo deportivo|multicancha|estadio|gimnasio|polideportivo|"
    r"sede social|club deportivo|pavimentos? participativos?|\bpmu\b|conjunto habitacional|"
    r"infraestructura (?:vial|publica|deportiva|educacional|sanitaria|hospitalaria|comunitaria|portuaria|"
    r"aeroportuaria|de riego|de salud|urbana|penitenciaria)"
)
# Verbo de obra + objeto de obra cercano (p. ej. "reposición de calzada", "habilitación espacios deportivos").
AFIN_OBRA = re.compile(
    r"\b(?:reposicion|mejoramiento|ampliacion|habilitacion|normalizacion|restauracion|remodelacion|"
    r"rehabilitacion|conservacion)\b.{0,60}?\b(?:" + _OBJETOS_OBRA + r")\b"
)
AFIN_MANT = re.compile(
    r"\b(?:mantencion|mantenimiento|reparacion)\b.{0,60}?\b(?:" + _OBJETOS_MANT + r")\b"
)
# Organismos de obras públicas: se consideran de obra, salvo que el nombre diga otra cosa.
SECTORES_AFIN = {"Obras Públicas"}
NO_OBRA = re.compile(
    r"aseo|vigilancia|guardias?|arriendo de (?:vehiculos?|camionetas?|buses|bus|camion|camiones)|"
    r"alimentacion|colacion|computador|notebook|insumos?|licencias?|software|combustible|"
    r"pasajes? (?:aereos?|de bus|de avion|terrestres?)|seguro|capacitacion|mobiliario|impresora|fotocopiad|telefon|internet|uniformes?|vestuario|"
    r"transporte|traslado|evento|navidad|medicament|farmac|dental|odontolog|laboratorio|ambulancia|"
    r"fumigac|sanitizac|desratizac|examenes|reactivos?|oxigeno|ropa|calzado|"
    r"vehiculos?|camionetas?|automoviles?|\bbuses\b|equipos? (?:medicos|computacionales|informaticos|de computacion)"
)
# Nombres que EMPIEZAN con un verbo de obra (así se nombran casi todas las obras públicas:
# "MEJORAMIENTO ...", "REPOSICION ...", "CONSERVACION ...", "CONSTRUCCION ...").
INICIO_OBRA = re.compile(
    r"^\W*(?:mejoramiento|reposicion|conservacion|construccion|ampliacion|habilitacion|normalizacion|"
    r"restauracion|remodelacion|rehabilitacion)\b"
)
# ...salvo que el resto del nombre muestre que es otra cosa (gestión, procesos, documentos...).
NO_INICIO_OBRA = re.compile(
    r"gestion|procesos?|calidad|clima|informatic|software|datos|capacidades|competencias|salud mental|"
    r"atencion|documentos?|archivos?|alimentos|biodiversidad|stock|medicamentos?"
)
# Materiales de construcción: los términos "fuertes" bastan solos; los genéricos (pintura, puertas, tuberías...)
# solo cuentan cuando la licitación es de suministro/compra, para no confundir un servicio con un material.
MAT_FUERTE = re.compile(
    r"material(?:es)? de (?:construccion|ferreteria|obra)|ferreteria|hormigon|\bcementos?\b|\baridos?\b|\bripio\b|\bgravilla\b|"
    r"\bestabilizado\b|\bfierros?\b|enfierradura|malla acma|volcanita|fibrocemento|ceramic[oa]s?|porcelanato|"
    r"baldosas?|ladrillos?|adoquines?|geotextil|gaviones?|prefabricad[oa]s?|emulsion asfaltica|"
    r"planchas? de (?:yeso|osb|zinc)|bloques? de hormigon|material(?:es)? granular(?:es)?|base granular|"
    r"base estabilizada|chancad[oa]|bischofita|carpeta asfaltica|mezcla asfaltica"
)
MAT_GENERICO = re.compile(
    r"tuberias?|canerias?|\bpvc\b|\bhdpe\b|artefactos? sanitarios?|griferia|\bpinturas?\b|esmaltes?|barniz|"
    r"impermeabilizante|\bmaderas?\b|\btableros?\b|\bpuertas?\b|\bventanas?\b|\bvidrios?\b|aislacion|"
    r"poliestireno|lana mineral|cables? electricos?|conductor(?:es)? electricos?|\bluminarias?\b|\bpostes?\b|"
    r"herramientas? (?:manuales|electricas|de construccion)|\bclavos\b|\bpernos\b|\bzinc\b|"
    r"senaletica vial|senalizacion vial|barreras? (?:de contencion|new jersey)|defensas? camineras?"
)
SUMINISTRO = re.compile(r"\b(?:suministros?|adquisicion|compra|provision|venta)\b")
# Maquinaria y equipos de obra.
MAQUINARIA = re.compile(
    r"maquinaria pesada|retroexcavadoras?|excavadoras?|motoniveladoras?|bulldozer|cargador(?:es)? frontal|"
    r"minicargador|rodillo (?:compactador|vibratorio)|andamios?|hormigoneras?|betoneras?|compactadoras?|"
    r"camion (?:tolva|pluma|grua)|\bgrua\b|grupo electrogeno|bombas? de hormigon"
)
# Estudios, ingeniería, ITO y planificación territorial.
ESTUDIOS = re.compile(
    r"topograf|mecanica de suelos|geotecni|calicatas?|sondajes?|ensayos? de (?:materiales|hormigon|suelos|laboratorio vial)|"
    r"control de calidad de (?:obras|hormigon|materiales)|inspeccion tecnica|\bito\b|asesoria a la inspeccion|"
    r"calculo estructural|proyectos? de (?:especialidades|arquitectura|ingenieria|electric|sanitari|climatizacion)|"
    r"diseno (?:estructural|sanitario|electrico|de ingenieria|de arquitectura)|"
    r"estudios? de (?:ingenieria|prefactibilidad|factibilidad|mecanica de suelos|impacto)|preinversion|\bpreinv\b|"
    r"consultoria de (?:ingenieria|arquitectura|obras?|diseno)|servicio de consultoria de diseno|fotogrametri|regularizacion de (?:vivienda|edificacion)|"
    r"plan(?:o)? regulador|planificacion territorial|\bprc\b|seccional"
)

# Versión de las reglas: si cambia, los registros guardados se vuelven a consultar y reclasificar.
REGLAS_VERSION = 6
LINEAS = ("Obras", "Materiales y suministros", "Maquinaria y equipos", "Estudios y servicios técnicos")
MOTIVOS = ("codigo", "nombre", "organismo", "descripcion")


def _material(t: str) -> bool:
    return bool(MAT_FUERTE.search(t) or (MAT_GENERICO.search(t) and SUMINISTRO.search(t)))


def _senales(t: str) -> set:
    """Señales de construcción distintas que aparecen en un texto."""
    rxs = (AFIN_FUERTE, AFIN_OBRA, AFIN_MANT, MAT_FUERTE, MAQUINARIA, ESTUDIOS)
    return {m.group(0) for rx in rxs for m in rx.finditer(t)}


# Los rubros de Mercado Público son categorías generales (p. ej. «Transporte de pasajeros por
# carretera»). Solo se usan para clasificar los que hablan de construcción o de insumos de obra;
# el resto se ignora, porque palabras sueltas como «carretera» o «ruta» llevaban a clasificar mal.
RUBRO_UTIL = re.compile(
    r"construc|edificac|ingenier|arquitect|\bobras?\b|urbaniz|hormig|cemento|asfalt|pavimen|ferreter|"
    r"tuberi|sanitari|electric|iluminac|alumbrado|maquinaria|excavac|cantera|aridos|madera|estructur|"
    r"topograf|geolog|pintura|aislante|techumbre|ventana|puerta|cerradura|griferi|ladrillo|vidrio|"
    r"agua potable|riego|herramienta|andamio|grua|equipo de (?:construccion|movimiento)|material(?:es)? de"
)


SENAL_DEBIL = {"ruta", "carretera"}


def rubros_utiles(rubros):
    return [r for r in (rubros or []) if RUBRO_UTIL.search(normalizar(r))]


def motivo_afin(nombre: str, rubros, sector: str, descripcion: str = ""):
    """Por qué una licitación se considera de construcción (o None si no lo es):
    'nombre' (frases de obra, materiales, maquinaria o estudios en el nombre o en rubros de construcción),
    'organismo' (MOP y similares), 'descripcion' (al menos dos señales distintas en la descripción)."""
    t_solo_nombre = normalizar(nombre)
    t_nom = normalizar(f"{nombre} {' '.join(rubros_utiles(rubros))}")
    # Si el nombre habla de transporte, aseo, insumos, etc., solo cuenta como construcción si además
    # trae una señal clara de obra, material, maquinaria o estudio (no basta «ruta» o «carretera»,
    # que también aparecen en servicios de transporte). Los rubros y la descripción no lo rescatan.
    if NO_OBRA.search(t_solo_nombre):
        if (_senales(t_solo_nombre) - SENAL_DEBIL) or _material(t_solo_nombre):
            return "nombre"
        return None
    if _senales(t_solo_nombre) or _material(t_solo_nombre):
        return "nombre"
    if _senales(t_nom) or _material(t_nom):
        return "nombre"
    if INICIO_OBRA.match(t_solo_nombre) and not NO_INICIO_OBRA.search(t_nom):
        return "nombre"
    if sector in SECTORES_AFIN:
        return "organismo"
    if descripcion and len(_senales(normalizar(descripcion))) >= 2:
        return "descripcion"
    return None


def es_afin_texto(nombre: str, rubros, sector: str, descripcion: str = "") -> bool:
    return motivo_afin(nombre, rubros, sector, descripcion) is not None


def linea_de(nombre: str, rubros) -> str:
    """Línea de negocio de una licitación de construcción: obras, materiales, maquinaria o estudios."""
    t = normalizar(f"{nombre} {' '.join(rubros_utiles(rubros))}")
    if ESTUDIOS.search(t):
        return LINEAS[3]
    obra = AFIN_FUERTE.search(t) or AFIN_OBRA.search(t) or AFIN_MANT.search(t)
    if _material(t) and (SUMINISTRO.search(t) or not obra):
        return LINEAS[1]
    if MAQUINARIA.search(t) and not obra:
        return LINEAS[2]
    return LINEAS[0]


# Clasificación referencial del organismo comprador: (patrón, sector, ministerio).
# El primer patrón que coincida define el resultado. Editable.
REGLAS_SECTOR = [
    (r"gobierno regional|\bgore\b", "Gobierno Regional", None),
    (r"municipal|\bdaem\b|\bcormu\b", "Municipalidades", None),
    (r"servicio de salud|hospital|cesfam|cecosf|\bsapu\b|subsecretaria de salud|seremi de salud|"
     r"cenabast|fonasa|instituto de salud publica", "Salud", "Ministerio de Salud"),
    (r"serviu|vivienda y urbanismo|minvu|seremi de vivienda", "Vivienda y Urbanismo",
     "Ministerio de Vivienda y Urbanismo"),
    (r"obras publicas|vialidad|direccion de arquitectura|obras hidraulicas|direccion general de aguas|"
     r"agua potable rural|concesiones|direccion de planeamiento|obras portuarias|direccion de aeropuertos|"
     r"\bmop\b", "Obras Públicas", "Ministerio de Obras Públicas"),
    (r"universidad|instituto profesional|centro de formacion tecnica|\bcft\b|\bduoc\b|\binacap\b",
     "Educación superior", None),
    (r"junji|junaeb|integra|servicio local de educacion|\bslep\b|educacion|escuela|liceo|colegio|"
     r"jardin infantil", "Educación", "Ministerio de Educación"),
    (r"carabineros|policia de investigaciones|\bpdi\b|ejercito|armada|fuerza aerea|\bfach\b|defensa|"
     r"estado mayor|bomberos|senapred|onemi|delegacion presidencial|seguridad publica",
     "Defensa, seguridad y emergencias", None),
    (r"gendarmeria|registro civil|reinsercion|mejor ninez|sename|servicio medico legal|justicia|"
     r"tribunal|asistencia judicial|fiscalia|defensoria", "Justicia", None),
    (r"transportes|telecomunicaciones|ferrocarriles|\befe\b|sectra", "Transportes",
     "Ministerio de Transportes y Telecomunicaciones"),
    (r"agricultura|\bsag\b|indap|conaf|\binia\b|riego|\bcnr\b|forestal|\bodepa\b",
     "Agricultura", "Ministerio de Agricultura"),
    (r"energia|\bsec\b|\bcne\b", "Energía", "Ministerio de Energía"),
    (r"medio ambiente|evaluacion ambiental|\bsma\b|\bsea\b", "Medio Ambiente",
     "Ministerio del Medio Ambiente"),
    (r"bienes nacionales", "Bienes Nacionales", "Ministerio de Bienes Nacionales"),
    (r"corfo|sercotec|sernatur|turismo|economia|pesca|mineria|sernageomin|enami",
     "Economía, minería y fomento", None),
    (r"desarrollo social|fosis|senama|senadis|injuv|conadi", "Desarrollo social y familia",
     "Ministerio de Desarrollo Social y Familia"),
    (r"deporte|\bind\b|cultura|patrimonio", "Deporte, cultura y patrimonio", None),
    (r"trabajo|sence|previsional|\bips\b|seguridad social", "Trabajo y previsión",
     "Ministerio del Trabajo y Previsión Social"),
    (r"hacienda|servicio de impuestos|tesoreria|aduanas|dipres|chilecompra|compras publicas|contraloria",
     "Hacienda y control", None),
]


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def normalizar(texto) -> str:
    """Minúsculas, sin tildes ni apóstrofes, espacios simples."""
    t = unicodedata.normalize("NFD", str(texto or ""))
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").lower()
    t = t.replace("'", "").replace("’", "").replace("`", "")
    return re.sub(r"\s+", " ", t).strip()


def ocultar(texto) -> str:
    """Quita el ticket de cualquier texto antes de imprimirlo."""
    t = str(texto)
    if TICKET:
        t = t.replace(TICKET, "***")
    return re.sub(r"(ticket=)[^&\s'\"]+", r"\1***", t, flags=re.I)


def log(*partes) -> None:
    print(ocultar(" ".join(str(p) for p in partes)), flush=True)


def fecha_iso(valor) -> str | None:
    """'2026-11-20T15:00:00.000' -> '2026-11-20T15:00:00'."""
    if not valor:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})", str(valor))
    if m:
        return f"{m.group(1)}T{m.group(2)}"
    m = re.match(r"(\d{4}-\d{2}-\d{2})$", str(valor))
    return f"{m.group(1)}T23:59:59" if m else None


def es_numero(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# --------------------------------------------------------------------------- #
# Acceso a la API
# --------------------------------------------------------------------------- #
class ErrorApi(Exception):
    pass


class ErrorTicket(Exception):
    pass


class ErrorCuota(Exception):
    pass


SESION = requests.Session()
SESION.headers.update({"User-Agent": "CChC-OHiggins-PanelLicitaciones/1.0"})


def consultar(params: dict, intentos: int = 5) -> dict:
    """Consulta la API con reintentos. Nunca deja el ticket en mensajes de error."""
    ultimo = "sin respuesta"
    for n in range(1, intentos + 1):
        try:
            r = SESION.get(ENDPOINT, params={**params, "ticket": TICKET}, timeout=60)
            try:
                datos = r.json()
            except ValueError:
                datos = None
            if isinstance(datos, dict) and "Listado" in datos and r.status_code < 400:
                return datos
            mensaje = ""
            if isinstance(datos, dict):
                mensaje = str(datos.get("Mensaje") or datos.get("mensaje") or "")
            bajo = normalizar(mensaje)
            if "simult" not in bajo:
                if "ticket" in bajo and any(p in bajo for p in ("invalid", "no valid", "no existe", "inexistente")):
                    raise ErrorTicket(mensaje)
                if any(p in bajo for p in ("excedido", "limite", "maximo de", "cuota")):
                    raise ErrorCuota(mensaje)
            ultimo = f"HTTP {r.status_code}: {mensaje or r.text[:120]}"
        except (ErrorTicket, ErrorCuota):
            raise
        except requests.RequestException as e:
            ultimo = f"{type(e).__name__}: {e}"
        if n < intentos:
            time.sleep(min(60, 2 * n * n))  # 2, 8, 18, 32 s
    raise ErrorApi(ocultar(ultimo))


# --------------------------------------------------------------------------- #
# Lógica de negocio
# --------------------------------------------------------------------------- #
def es_ohiggins(region, comuna) -> bool:
    r = normalizar(region)
    if r:
        return "higgins" in r or r.startswith("vi region") or "sexta region" in r
    return normalizar(comuna) in COMUNAS_OHIGGINS


def clasificar(organismo: str, unidad: str):
    texto = normalizar(f"{organismo} {unidad}")
    for patron, sector, ministerio in REGLAS_SECTOR:
        if re.search(patron, texto):
            return sector, ministerio
    return "Otros organismos", None


def tipo_de(codigo: str, lic: dict):
    t = lic.get("Tipo")
    if not t:
        m = re.search(r"-([A-Z0-9]{2})\d{2}$", codigo or "")
        t = m.group(1) if m else None
    return t, TIPOS.get(t, t)


ESTADOS = {5: "Publicada", 6: "Cerrada", 7: "Desierta", 8: "Adjudicada", 15: "Revocada", 18: "Suspendida", 19: "Suspendida"}


def estado_de(lic: dict, item_lista: dict) -> str:
    """Estado del proceso según Mercado Público (las licitaciones activas llegan como 'Publicada')."""
    texto = str(lic.get("Estado") or "").strip()
    if texto:
        return texto
    try:
        return ESTADOS.get(int(lic.get("CodigoEstado") or item_lista.get("CodigoEstado")), "Publicada")
    except (TypeError, ValueError):
        return "Publicada"


def construir_registro(lic: dict, item_lista: dict, hoy: str) -> dict:
    codigo = lic.get("CodigoExterno") or item_lista.get("CodigoExterno")
    comp = lic.get("Comprador") or {}
    fechas = lic.get("Fechas") or {}
    items = (lic.get("Items") or {}).get("Listado") or []

    organismo = (comp.get("NombreOrganismo") or "").strip()
    unidad = (comp.get("NombreUnidad") or "").strip()
    sector, ministerio = clasificar(organismo, unidad)

    rubros, afin_codigo = [], False
    for it in items:
        nombre = (it.get("Categoria") or "").strip()
        if nombre and nombre not in rubros:
            rubros.append(nombre)
        for campo in ("CodigoCategoria", "CodigoProducto"):
            cod = re.sub(r"\D", "", str(it.get(campo) or ""))
            if len(cod) >= 8 and cod.startswith(AFIN_PREFIJOS):   # UNSPSC de 8 dígitos
                afin_codigo = True
    nombre_lic = (lic.get("Nombre") or item_lista.get("Nombre") or "").strip()
    descripcion = (lic.get("Descripcion") or "").strip()[:1500]
    motivo = "codigo" if afin_codigo else motivo_afin(nombre_lic, rubros, sector, descripcion)
    afin = motivo is not None

    monto = lic.get("MontoEstimado")
    monto = monto if es_numero(monto) and monto > 0 else None
    tipo, tipo_nombre = tipo_de(codigo, lic)
    cierre = fecha_iso(lic.get("FechaCierre") or fechas.get("FechaCierre") or item_lista.get("FechaCierre"))

    return {
        "id": codigo,
        "nombre": nombre_lic,
        "descripcion": descripcion,
        "tipo": tipo,
        "tipo_nombre": tipo_nombre,
        "organismo": organismo,
        "unidad": unidad,
        "comuna": (comp.get("ComunaUnidad") or "").strip(),
        "region": (comp.get("RegionUnidad") or "").strip(),
        "sector": sector,
        "ministerio": ministerio,
        "rubros": rubros[:6],
        "afin": afin,
        "afin_cod": afin_codigo,   # parte por código de rubro; el resto se reevalúa en cada ejecución
        "afin_motivo": motivo,
        "linea": linea_de(nombre_lic, rubros) if afin else None,
        "reglas": REGLAS_VERSION,
        "monto": monto,
        "moneda": (lic.get("Moneda") or "").strip() or None,
        "publicacion": fecha_iso(fechas.get("FechaPublicacion")),
        "estado": estado_de(lic, item_lista),
        "cierre": cierre,
        "url": URL_FICHA.format(codigo=codigo),
    }


def construir_entrada(lic: dict, item_lista: dict, hoy: str) -> dict:
    comp = lic.get("Comprador") or {}
    if not es_ohiggins(comp.get("RegionUnidad"), comp.get("ComunaUnidad")):
        return {"r": 0, "t": hoy}
    return {"r": 1, "t": hoy, "d": construir_registro(lic, item_lista, hoy)}


# --------------------------------------------------------------------------- #
# Caché
# --------------------------------------------------------------------------- #
def cargar_cache() -> dict:
    if RUTA_CACHE.exists():
        try:
            c = json.loads(RUTA_CACHE.read_text(encoding="utf-8"))
            if isinstance(c, dict) and isinstance(c.get("detalles"), dict):
                return c
        except (OSError, ValueError):
            log("Aviso: no se pudo leer la caché; se reconstruirá desde cero.")
    return {"version": 1, "detalles": {}}


def escribir_atomico(ruta: Path, contenido: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(contenido, encoding="utf-8")
    tmp.replace(ruta)


def guardar_cache(cache: dict) -> None:
    escribir_atomico(RUTA_CACHE, json.dumps(cache, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


# --------------------------------------------------------------------------- #
# Programa principal
# --------------------------------------------------------------------------- #
def main() -> int:
    if not TICKET:
        log("ERROR: falta la variable MERCADO_PUBLICO_TICKET (secreto del repositorio).")
        return 2
    if not (BASE_URL.startswith("https://") or re.match(r"http://(127\.0\.0\.1|localhost)(:\d+)?$", BASE_URL)):
        log("ERROR: MP_BASE_URL debe ser https (o http solo hacia localhost).")
        return 2

    ahora = datetime.now(TZ)
    hoy = ahora.strftime("%Y-%m-%d")
    ahora_iso = ahora.strftime("%Y-%m-%dT%H:%M:%S")

    log("Consultando licitaciones activas del país...")
    try:
        lista = consultar({"estado": "activas"})
    except ErrorTicket as e:
        log(f"ERROR: la API rechazó el ticket ({e}). Revise el secreto MERCADO_PUBLICO_TICKET.")
        return 3
    except (ErrorCuota, ErrorApi) as e:
        log(f"ERROR: no se pudo obtener el listado de activas: {e}")
        return 1

    activas = {}
    for it in lista.get("Listado") or []:
        cod = it.get("CodigoExterno")
        if cod:
            activas[cod] = it
    if not activas:
        log("La API devolvió 0 licitaciones activas; no se modifican los datos.")
        return 1
    log(f"Licitaciones activas en el país: {len(activas)}")

    cache = cargar_cache()
    detalles = cache["detalles"]
    inicializado = bool(cache.get("inicializado"))
    for cod in [c for c in detalles if c not in activas]:  # ya no están activas
        del detalles[cod]

    for e in detalles.values():   # registros guardados con otra versión de las reglas: se vuelven a consultar
        if e.get("r") == 1 and (e.get("d") or {}).get("reglas") != REGLAS_VERSION:
            e["t"] = "2000-01-01"

    limite_refresco = (ahora - timedelta(days=REFRESCO_DIAS)).strftime("%Y-%m-%d")
    refrescar = [c for c, e in detalles.items() if e.get("r") == 1 and e.get("t", hoy) < limite_refresco]
    nuevas = [c for c in activas if c not in detalles]
    pendientes = refrescar + nuevas
    log(f"Por consultar en detalle: {len(nuevas)} nuevas + {len(refrescar)} a refrescar")

    consultadas = fallidas = seguidas = 0
    parcial, motivo = False, ""
    inicio_detalles = time.monotonic()
    for cod in pendientes:
        if consultadas >= MAX_DETALLES:
            parcial, motivo = True, "se alcanzó el tope de consultas de esta ejecución"
            break
        if (time.monotonic() - inicio_detalles) / 60 >= MAX_MINUTOS:
            parcial, motivo = True, f"se alcanzó el tiempo máximo de la ejecución ({MAX_MINUTOS:g} min); el resto sigue en la próxima"
            break
        try:
            resp = consultar({"codigo": cod})
        except ErrorTicket as e:
            log(f"ERROR: la API rechazó el ticket ({e}).")
            guardar_cache(cache)
            return 3
        except ErrorCuota as e:
            parcial, motivo = True, f"cupo diario de la API agotado ({e})"
            break
        except ErrorApi as e:
            fallidas += 1
            seguidas += 1
            log(f"Aviso: falló {cod}: {e}")
            if seguidas >= 10:
                parcial, motivo = True, "demasiados errores seguidos de la API"
                break
            continue
        seguidas = 0
        consultadas += 1
        listado = resp.get("Listado") or []
        if listado:
            era_nueva = cod not in detalles
            previa = detalles.get(cod, {})
            entrada = construir_entrada(listado[0], activas[cod], hoy)
            # "v" = fecha en que el panel vio la licitación por primera vez. Solo se marca
            # cuando la caché ya estaba completa (si no, la primera carga entera sería "nueva").
            if entrada.get("r") == 1:
                if era_nueva and inicializado:
                    entrada["v"] = hoy
                elif previa.get("v"):
                    entrada["v"] = previa["v"]
            detalles[cod] = entrada
        else:
            fallidas += 1
        if consultadas % 100 == 0:
            guardar_cache(cache)
            log(f"  ...{consultadas} consultadas")
        time.sleep(PAUSA_SEG)
    guardar_cache(cache)

    sin_clasificar = sum(1 for c in activas if c not in detalles)
    if not sin_clasificar and not inicializado:  # primera carga completa: desde ahora se marcan las nuevas
        cache["inicializado"] = True
        guardar_cache(cache)
    if sin_clasificar:
        parcial = True
        motivo = motivo or "algunas consultas fallaron y se reintentarán en la próxima ejecución"
    log(f"Detalles consultados: {consultadas}; fallidos: {fallidas}; sin clasificar: {sin_clasificar}")
    if parcial:
        log(f"Actualización PARCIAL: {motivo}.")

    registros = []
    for cod, e in detalles.items():
        if e.get("r") != 1 or cod not in activas:
            continue
        reg = dict(e["d"])
        reg["visto"] = e.get("v")
        # Las reglas de texto pueden mejorar entre ejecuciones: se reevalúan sobre lo ya guardado.
        if reg.get("reglas") != REGLAS_VERSION:      # aún sin reclasificar (ejecución parcial): se mantiene lo anterior
            motivo = "nombre" if reg.get("afin") else None
        elif reg.get("afin_cod"):
            motivo = "codigo"
        else:
            motivo = motivo_afin(reg.get("nombre", ""), reg.get("rubros"), reg.get("sector", ""), reg.get("descripcion", ""))
        reg["afin"] = motivo is not None
        reg["afin_motivo"] = motivo
        reg["linea"] = linea_de(reg.get("nombre", ""), reg.get("rubros")) if motivo else None
        cierre_lista = fecha_iso(activas[cod].get("FechaCierre"))
        if cierre_lista:  # el listado trae el cierre vigente (por si se amplió el plazo)
            reg["cierre"] = cierre_lista
        if reg.get("cierre") and reg["cierre"] < ahora_iso:  # ya no recibe ofertas
            continue
        registros.append(reg)
    registros.sort(key=lambda r: (r.get("cierre") or "9999", r.get("id") or ""))

    # Historial: cada licitación de la región queda registrada (aunque luego cierre), con su fecha de
    # publicación, monto en CLP y si es de construcción. Con él se arma la evolución mensual.
    historial = cache.setdefault("historial", {})
    cache.setdefault("historial_desde", hoy)
    for r in registros:
        pub = (r.get("publicacion") or r.get("visto") or hoy)[:10]
        clp = r["monto"] if (r.get("moneda") in (None, "CLP", "Peso Chileno") and r.get("monto")) else 0
        historial[r["id"]] = [pub, int(clp), 1 if r.get("afin") else 0]
    guardar_cache(cache)

    salida = {
        "generado": datetime.now(TZ).isoformat(timespec="seconds"),   # hora de término, no de inicio
        "generado_texto": datetime.now(TZ).strftime("%d-%m-%Y %H:%M"),
        "fuente": "Dirección ChileCompra - API de Mercado Público",
        "total_activas_pais": len(activas),
        "total_region": len(registros),
        "sin_clasificar": sin_clasificar,
        "parcial": parcial,
        "demo": DEMO,
        "historial_desde": cache.get("historial_desde"),
        "historial": sorted(historial.values()),
        "licitaciones": registros,
    }
    escribir_atomico(RUTA_SALIDA, json.dumps(salida, ensure_ascii=False, separators=(",", ":")))
    log(f"Listo: {len(registros)} licitaciones vigentes en la Región de O'Higgins.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
