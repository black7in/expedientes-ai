import asyncio
import hashlib
import json
import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from ..core.catalogos import (
    TIPOS_MEMORIAL, TIPOS_ACCION, MATERIAS, INSTANCIAS,
    FORMATOS, ESTILOS, ORIGENES,
)
from .embedding_service import EmbeddingService
from .extractor import extraer_html
from .html_cleaner import limpiar_html, validar_html, texto_para_hash
from .llm_client import completar



_SYSTEM_PROMPT = """\
Eres un asistente especializado en procesamiento de documentos legales bolivianos.
Tu tarea es transformar un memorial jurídico en una PLANTILLA reutilizable.

El documento de entrada está en HTML semántico con estos elementos: <h1>, <h2>, <p>, <strong>, <em>, <ul>, <ol>, <li>.
Una PLANTILLA conserva la estructura HTML, el tono y la forma del memorial original, pero
elimina TODA información identificable o específica del caso.

Debes devolver EXCLUSIVAMENTE un objeto JSON válido, sin texto adicional antes
o después. No uses bloques de código markdown. No expliques nada."""


def _build_user_prompt(documento: str) -> str:
    tm  = ", ".join(f'"{t}"' for t in TIPOS_MEMORIAL)
    ta  = ", ".join(f'"{t}"' for t in TIPOS_ACCION)
    mat = ", ".join(f'"{m}"' for m in MATERIAS)
    ins = ", ".join(f'"{i}"' for i in INSTANCIAS)
    fmt = ", ".join(f'"{f}"' for f in FORMATOS)
    est = ", ".join(f'"{e}"' for e in ESTILOS)

    return f"""\
Procesa el siguiente memorial y devuelve un JSON con esta estructura exacta:

{{
  "texto_anonimizado": "...",
  "resumen_caso": "...",
  "tipo_memorial": "...",
  "tipo_memorial_propuesto": null,
  "tipo_accion": "...",
  "tipo_accion_propuesto": null,
  "subtipo": null,
  "materia": "...",
  "instancia": "...",
  "formato": "...",
  "estilo": "...",
  "extension": "...",
  "advertencias": []
}}

REGLAS DE ANONIMIZACIÓN (campo "texto_anonimizado"):
El input es HTML semántico. Anonimiza SOLO el contenido de texto dentro de las etiquetas,
SIN modificar ni eliminar ninguna etiqueta HTML. Las etiquetas deben quedar exactamente igual.

Ejemplo:
ENTRADA:  <p><strong>PRIMERO.-</strong> En fecha 12 de marzo de 2024, el suscrito Carlos Mendoza presentó...</p>
SALIDA:   <p><strong>PRIMERO.-</strong> En fecha [FECHA], el suscrito [NOMBRE_PARTE] presentó...</p>

Datos a reemplazar con sus placeholders:
- Nombres de personas físicas → [NOMBRE_PARTE]
  Si puedes inferir el rol procesal: [NOMBRE_DEMANDANTE], [NOMBRE_DEMANDADO],
  [NOMBRE_QUERELLANTE], [NOMBRE_IMPUTADO], [NOMBRE_TESTIGO], [NOMBRE_ABOGADO]
- Cédulas de identidad, NIT, RUT → [DOCUMENTO_IDENTIDAD]
- Montos en cualquier moneda → [MONTO]
- Fechas concretas (día/mes/año o "12 de marzo de 2024") → [FECHA]
- Direcciones, ubicaciones específicas → [DIRECCIÓN]
- Números de escritura, expediente, notaría, registro → [REF_DOCUMENTAL]
- Nombres de empresas o instituciones específicas (no genéricas) → [ENTIDAD]
- Números de teléfono → [TELÉFONO]
- Correos electrónicos → [EMAIL]
- Placas vehiculares, números de serie, IMEI, etc. → [IDENTIFICADOR]

NO reemplaces:
- Nombres de leyes, códigos o normas (Código Civil, Ley 1970, etc.)
- Términos jurídicos genéricos (demandante, juzgado, fiscal, etc.)
- Nombres de instituciones genéricas (Ministerio Público, Tribunal Supremo)
- Artículos legales y sus números

REGLAS DE CLASIFICACIÓN:

"resumen_caso" — string de 1-2 oraciones (~20-40 palabras) que describa la
esencia del caso en lenguaje NEUTRO y DESCRIPTIVO. NO uses tecnicismos
rituales ni la narrativa formal del memorial. Sirve para hacer match
semántico con la narración cruda del usuario.

"tipo_memorial" — elegir UNO de: {tm}

"tipo_accion" — elegir UNO de: {ta}

REGLA DE CATÁLOGO ABIERTO:
Si NINGÚN valor del catálogo describe correctamente el documento, devuelve:
  "tipo_accion": "otro",
  "tipo_accion_propuesto": "<nombre_snake_case>"
(o equivalente para tipo_memorial)

El valor propuesto debe:
- Estar en snake_case (sin espacios ni mayúsculas).
- Tener máximo 40 caracteres.
- Ser descriptivo y específico (no "otro_civil").
- Reflejar terminología jurídica boliviana estándar.
Ejemplos: "cumplimiento_comodato", "nulidad_matrimonio", "interdicto_recobrar_posesion"

NO uses "otro" si un valor del catálogo SÍ aplica.
Si NO usas "otro", deja "tipo_accion_propuesto" como null.

"subtipo" — string libre opcional para refinamiento (ej: "estafa_agravada").
  Usar null si no aplica.

"materia" — elegir UNO de: {mat}

"instancia" — elegir UNO de: {ins}

"formato" — elegir UNO de: {fmt}
  "estructurado": secciones con títulos o numeración explícita en el CUERPO principal (ej: PRIMERO.-, SEGUNDO.-, I., II.)
  "corrido": prosa continua sin títulos en el cuerpo; los "Otrosí" al pie NO cuentan como estructura
  "mixto": combina ambos en el cuerpo del memorial

"estilo" — elegir UNO de: {est}

"extension" — elegir UNO de:
  "corta" (menos de 800 palabras), "media" (800-2500), "larga" (más de 2500)

"advertencias" — array con cero o más de:
  "anonimizacion_dudosa", "clasificacion_incierta", "documento_atipico",
  "contenido_sensible", "calidad_redaccion_baja"

DOCUMENTO A PROCESAR:
---
{documento}
---"""


_PROPUESTA_RE = re.compile(r'^[a-z][a-z0-9_]{0,39}$')

_REGEX_ANTIFUGA = [
    re.compile(r"\b\d{7,8}[\-\s]?[A-Z]{2}\b"),
    re.compile(r"\$\s?\d+"),
    re.compile(r"Bs\.?\s?\d+", re.IGNORECASE),
    re.compile(r"\b\d{1,2}\s+de\s+\w+\s+de\s+\d{4}\b"),
    re.compile(r"\b\+?(?:591)?\d{8}\b"),
]


async def indexar_plantilla(
    ruta_archivo: str,
    formato: str,
    origen: str,
    db: AsyncSession,
) -> dict:
    extraccion = await asyncio.to_thread(extraer_html, ruta_archivo, formato)
    html = limpiar_html(extraccion["html"])
    return await _procesar_plantilla(html, origen, db)


async def indexar_plantilla_desde_texto(
    texto: str,
    origen: str,
    db: AsyncSession,
) -> dict:
    parrafos = [f"<p>{p.strip()}</p>" for p in texto.split("\n\n") if p.strip()]
    html = limpiar_html("\n".join(parrafos))
    return await _procesar_plantilla(html, origen, db)


async def _procesar_plantilla(html: str, origen: str, db: AsyncSession) -> dict:
    if not html or len(html.strip()) < 500:
        raise ValueError("El documento es demasiado corto (mínimo 500 caracteres).")

    content_hash = hashlib.sha256(texto_para_hash(html).encode()).hexdigest()
    dup = await db.execute(
        text("SELECT id FROM plantillas_memoriales WHERE content_hash = :h"),
        {"h": content_hash},
    )
    if dup.fetchone():
        raise ValueError("Este documento ya fue indexado anteriormente (contenido duplicado).")

    llm_result = await _preprocesar_con_llm(html)

    advertencias = _validar_salida_llm(llm_result, html)
    for adv in llm_result.get("advertencias", []):
        if adv not in advertencias:
            advertencias.append(adv)

    if _aplicar_regex_antifuga(llm_result["texto_anonimizado"]):
        if "anonimizacion_dudosa" not in advertencias:
            advertencias.append("anonimizacion_dudosa")

    embeddings = await asyncio.to_thread(
        EmbeddingService.encode_passages, [llm_result["resumen_caso"]]
    )
    emb_str = EmbeddingService.to_pgvector_str(embeddings[0])

    tipo_memorial_prop = llm_result.get("tipo_memorial_propuesto")
    tipo_accion_prop   = llm_result.get("tipo_accion_propuesto")
    hay_propuesta      = llm_result["tipo_memorial"] == "otro" or llm_result["tipo_accion"] == "otro"

    advertencias_criticas = {"anonimizacion_dudosa", "contenido_sensible"}
    activo = (
        origen != "usuario_subido"
        and not advertencias_criticas.intersection(advertencias)
    )

    plantilla_id = str(uuid.uuid4())

    await db.execute(
        text("""
            INSERT INTO plantillas_memoriales (
                id, content_hash, contenido, resumen_caso, embedding,
                tipo_memorial, tipo_memorial_propuesto,
                tipo_accion,   tipo_accion_propuesto,
                subtipo, materia, instancia,
                formato, estilo, extension, origen, calidad, activo
            ) VALUES (
                CAST(:id AS uuid), :content_hash, :contenido, :resumen_caso, CAST(:embedding AS vector),
                :tipo_memorial, :tipo_memorial_propuesto,
                :tipo_accion,   :tipo_accion_propuesto,
                :subtipo, :materia, :instancia,
                :formato, :estilo, :extension, :origen, 'estandar', :activo
            )
        """),
        {
            "id":                       plantilla_id,
            "content_hash":             content_hash,
            "contenido":                llm_result["texto_anonimizado"],
            "resumen_caso":             llm_result["resumen_caso"],
            "embedding":                emb_str,
            "tipo_memorial":            llm_result["tipo_memorial"],
            "tipo_memorial_propuesto":  tipo_memorial_prop,
            "tipo_accion":              llm_result["tipo_accion"],
            "tipo_accion_propuesto":    tipo_accion_prop,
            "subtipo":                  llm_result.get("subtipo"),
            "materia":                  llm_result["materia"],
            "instancia":                llm_result["instancia"],
            "formato":                  llm_result["formato"],
            "estilo":                   llm_result.get("estilo"),
            "extension":                llm_result.get("extension"),
            "origen":                   origen,
            "activo":                   activo,
        },
    )

    for campo, propuesto in [
        ("tipo_memorial", tipo_memorial_prop),
        ("tipo_accion",   tipo_accion_prop),
    ]:
        if propuesto:
            await db.execute(
                text("""
                    INSERT INTO catalogo_propuestas (campo, valor_propuesto, frecuencia, doc_ids)
                    VALUES (:campo, :propuesto, 1, ARRAY[:doc_id])
                    ON CONFLICT (campo, valor_propuesto) DO UPDATE
                        SET frecuencia = catalogo_propuestas.frecuencia + 1,
                            doc_ids    = array_append(catalogo_propuestas.doc_ids, :doc_id)
                    WHERE catalogo_propuestas.estado = 'pendiente'
                """),
                {"campo": campo, "propuesto": propuesto, "doc_id": plantilla_id},
            )

    await db.commit()

    return {
        "id":                      plantilla_id,
        "activo":                  activo,
        "tipo_memorial":           llm_result["tipo_memorial"],
        "tipo_memorial_propuesto": tipo_memorial_prop,
        "tipo_accion":             llm_result["tipo_accion"],
        "tipo_accion_propuesto":   tipo_accion_prop,
        "materia":                 llm_result["materia"],
        "instancia":               llm_result["instancia"],
        "resumen_caso":            llm_result["resumen_caso"],
        "advertencias":            advertencias,
    }


async def _preprocesar_con_llm(html: str) -> dict:
    user_msg = _build_user_prompt(html)
    respuesta_anterior = ""
    ultimo_error = ""

    for intento in range(3):
        if intento > 0:
            user_msg = (
                f"Tu respuesta anterior no era JSON válido. Error: {ultimo_error}\n\n"
                f"Respuesta anterior:\n{respuesta_anterior}\n\n"
                "Corrige el JSON y devuelve SOLO el JSON válido, sin texto adicional, sin markdown."
            )

        raw = await completar(system=_SYSTEM_PROMPT, user=user_msg, max_tokens=8192, temperature=0.1)
        respuesta_anterior = raw

        try:
            return _extraer_json(raw)
        except (json.JSONDecodeError, ValueError) as e:
            ultimo_error = str(e)
            if intento == 2:
                raise RuntimeError(
                    f"LLM no devolvió JSON válido tras 3 intentos. Último error: {ultimo_error}"
                ) from e

    raise RuntimeError("Error inesperado en _preprocesar_con_llm")


def _extraer_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise


def _validar_salida_llm(resultado: dict, texto_original: str) -> list[str]:
    advertencias: list[str] = []

    for campo in ("texto_anonimizado", "resumen_caso", "tipo_memorial", "tipo_accion", "materia", "instancia", "formato"):
        if not resultado.get(campo):
            raise ValueError(f"El LLM no devolvió el campo obligatorio: '{campo}'")

    for campo, catalogo in [("tipo_memorial", TIPOS_MEMORIAL), ("tipo_accion", TIPOS_ACCION)]:
        valor = resultado.get(campo, "")
        if valor not in catalogo:
            prop_key = f"{campo}_propuesto"
            if _PROPUESTA_RE.match(valor):
                resultado[prop_key] = valor
            else:
                resultado[prop_key] = None
            resultado[campo] = "otro"
            if "clasificacion_incierta" not in advertencias:
                advertencias.append("clasificacion_incierta")

    if resultado["materia"] not in MATERIAS:
        raise ValueError(f"materia '{resultado['materia']}' fuera de catálogo.")
    if resultado["instancia"] not in INSTANCIAS:
        raise ValueError(f"instancia '{resultado['instancia']}' fuera de catálogo.")
    if resultado["formato"] not in FORMATOS:
        raise ValueError(f"formato '{resultado['formato']}' fuera de catálogo.")

    for campo, campo_prop in [
        ("tipo_memorial", "tipo_memorial_propuesto"),
        ("tipo_accion",   "tipo_accion_propuesto"),
    ]:
        valor     = resultado.get(campo)
        propuesto = resultado.get(campo_prop)
        if valor == "otro":
            if propuesto and not _PROPUESTA_RE.match(propuesto):
                resultado[campo_prop] = None
            if not resultado.get(campo_prop) and "clasificacion_incierta" not in advertencias:
                advertencias.append("clasificacion_incierta")
        else:
            resultado[campo_prop] = None

    if not validar_html(resultado["texto_anonimizado"]):
        advertencias.append("html_invalido")

    if len(texto_original) > 0:
        ratio = len(resultado["texto_anonimizado"]) / len(texto_original)
        if ratio < 0.80 or ratio > 1.20:
            advertencias.append("anonimizacion_dudosa")

    return advertencias


def _aplicar_regex_antifuga(texto: str) -> list[str]:
    fugas: list[str] = []
    for patron in _REGEX_ANTIFUGA:
        fugas.extend(patron.findall(texto))
    return fugas
