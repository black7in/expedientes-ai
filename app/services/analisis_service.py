import json
import re
from typing import List, Literal, Optional

from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.catalogos import INSTANCIAS, MATERIAS, TIPOS_ACCION, TIPOS_MEMORIAL
from . import llm_client

_SYSTEM_PROMPT = """Eres un analista jurídico especializado en derecho boliviano.
Tu tarea es estructurar el relato de un caso jurídico en datos verificables
que servirán para generar un memorial.

Tus usuarios son abogados. Asume conocimiento técnico y terminología precisa.

NO recomiendes estrategia jurídica.
NO cites artículos ni jurisprudencia.
NO redactes nada en lenguaje formal de memorial.
NO completes datos que el usuario no proporcionó.

Devuelve EXCLUSIVAMENTE un JSON válido. Sin texto antes o después. Sin
bloques de código markdown."""

_USER_TEMPLATE = """Analiza el siguiente relato y devuelve un JSON con esta estructura exacta:

{{
  "inventario": [
    {{
      "id": 1,
      "fecha": "marzo 2024",
      "contenido": "...",
      "tipo": "contrato",
      "relevancia": "alta"
    }}
  ],
  "datos_faltantes": ["dirección del inmueble", "CI del demandado"],
  "partes": [
    {{
      "nombre": "Carlos Mendoza",
      "rol": "demandado",
      "tipo": "persona_natural",
      "datos_conocidos": ["nombre"],
      "datos_faltantes": ["CI", "domicilio"]
    }}
  ],
  "pretension": "...",
  "clasificacion": {{
    "tipo_memorial": "demanda",
    "tipo_memorial_propuesto": null,
    "tipo_accion": "ejecucion_hipotecaria",
    "tipo_accion_propuesto": null,
    "materia": "civil",
    "instancia": "primera",
    "confianza": 0.9,
    "justificacion": "..."
  }},
  "resumen_caso_query": "...",
  "keywords_legales": ["mutuo", "hipoteca", "mora"],
  "confianza_global": 0.85,
  "advertencias": []
}}

REGLAS:

INVENTARIO
- Un hecho = una unidad fáctica atómica (fecha + evento + actores).
- NO inferir hechos no mencionados.
- NO completar datos faltantes con suposiciones.
- "tipo" debe pertenecer al enum.
- "relevancia": alta si es central a la pretensión, media si es contexto útil, baja si es accesorio.

DATOS_FALTANTES
- Lista de información que típicamente requiere un memorial pero que NO está en el relato.
  Ej: "CI del demandado", "número de escritura", "dirección del inmueble".

PARTES
- Identificar a todos los sujetos relevantes con su rol procesal.
- "rol" según el tipo de acción inferida.

PRETENSION
- Una oración breve en lenguaje neutro que describa qué pide el usuario.
- Ej: "Ejecución de garantía hipotecaria y cobro del capital adeudado más intereses moratorios."

CLASIFICACION
- "tipo_memorial" y "tipo_accion" del catálogo provisto abajo.
- Si ninguno encaja, usar "otro" y completar el campo "_propuesto" en
  snake_case (max 40 chars, terminología jurídica boliviana estándar).
- "confianza" 0-1 según qué tan claro está el encuadre procesal.
- "justificacion" 1-2 oraciones, sin referencias a artículos.

RESUMEN_CASO_QUERY
- 1-2 oraciones (~30 palabras), lenguaje NEUTRO y DESCRIPTIVO.
- NO tecnicismos rituales. NO narrativa del usuario.
- Ejemplo: "Disputa por préstamo personal con garantía hipotecaria incumplido
  tras mora del deudor, el acreedor solicita ejecución."

KEYWORDS_LEGALES
- 3-8 términos jurídicos relevantes para búsqueda en códigos.
- Sustantivos jurídicos, no verbos. Ej: ["mutuo", "hipoteca", "mora"].

CONFIANZA_GLOBAL
- 0-1. Refleja qué tan completa y coherente fue la información para estructurar el caso.
- Si confianza < 0.6, NO inventes para subirla. Es válido devolver baja confianza.

ADVERTENCIAS
- Array de strings con flags relevantes:
  - "input_insuficiente": muy poco contexto para estructurar el caso.
  - "clasificacion_ambigua": tipo_accion no es claro.
  - "pretension_no_identificada": no se pudo deducir qué pide el usuario.
  - "contenido_sensible": menores, violencia sexual u otro tema delicado.

CATÁLOGOS:

tipo_memorial: {tipos_memorial}
tipo_accion: {tipos_accion}
materia: {materias}
instancia: {instancias}

REGLA DE CATÁLOGO ABIERTO:
Si ninguno del catálogo encaja para tipo_memorial o tipo_accion, usar
"otro" y completar el campo "_propuesto" en snake_case (max 40 chars,
terminología jurídica boliviana estándar).

RELATO A ANALIZAR:
---
{narracion}
---"""

_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]{2,39}$")

_TIPOS_HECHO_VALIDOS = {
    "contrato", "pago", "incumplimiento", "requerimiento",
    "garantia", "comunicacion", "hecho_ilicito", "otro",
}

# Alias que LLMs usan frecuentemente fuera del enum de rol
_ROL_ALIASES: dict[str, str] = {
    "demandada":   "demandado",
    "actora":      "demandante",
    "actor":       "demandante",
    "querellada":  "imputado",
    "querellado":  "imputado",
    "procesado":   "imputado",
    "procesada":   "imputado",
}
_ROLES_VALIDOS = {"demandante", "demandado", "querellante", "imputado", "testigo", "tercero"}

_TIPOS_PARTE_VALIDOS = {"persona_natural", "persona_juridica"}

_RELEVANCIA_VALIDA = {"alta", "media", "baja"}


# ---------------------------------------------------------------------------
# Modelos Pydantic
# ---------------------------------------------------------------------------

class Hecho(BaseModel):
    id: int
    fecha: Optional[str] = None
    contenido: str
    tipo: Literal[
        "contrato", "pago", "incumplimiento", "requerimiento",
        "garantia", "comunicacion", "hecho_ilicito", "otro",
    ]
    relevancia: Literal["alta", "media", "baja"]

    @field_validator("tipo", mode="before")
    @classmethod
    def normalizar_tipo(cls, v: str) -> str:
        return v if v in _TIPOS_HECHO_VALIDOS else "otro"

    @field_validator("relevancia", mode="before")
    @classmethod
    def normalizar_relevancia(cls, v: str) -> str:
        return v if v in _RELEVANCIA_VALIDA else "media"


class Parte(BaseModel):
    nombre: Optional[str] = None
    rol: Literal["demandante", "demandado", "querellante", "imputado", "testigo", "tercero"]
    tipo: Literal["persona_natural", "persona_juridica"]
    datos_conocidos: List[str]
    datos_faltantes: List[str]

    @field_validator("rol", mode="before")
    @classmethod
    def normalizar_rol(cls, v: str) -> str:
        v = _ROL_ALIASES.get(v, v)
        return v if v in _ROLES_VALIDOS else "tercero"

    @field_validator("tipo", mode="before")
    @classmethod
    def normalizar_tipo_parte(cls, v: str) -> str:
        return v if v in _TIPOS_PARTE_VALIDOS else "persona_natural"


class Clasificacion(BaseModel):
    tipo_memorial: str
    tipo_memorial_propuesto: Optional[str] = None
    tipo_accion: str
    tipo_accion_propuesto: Optional[str] = None
    materia: Literal[
        "civil", "penal", "laboral", "familiar",
        "constitucional", "administrativo", "comercial", "tributario",
    ]
    instancia: Literal["primera", "apelacion", "casacion"]
    confianza: float
    justificacion: str


class AnalisisCaso(BaseModel):
    inventario: List[Hecho]
    datos_faltantes: List[str]
    partes: List[Parte]
    pretension: str
    clasificacion: Clasificacion
    resumen_caso_query: str
    keywords_legales: List[str]
    confianza_global: float
    advertencias: List[str]

    @field_validator("keywords_legales", mode="before")
    @classmethod
    def normalizar_keywords(cls, v: list) -> list:
        return [k.lower() if isinstance(k, str) else k for k in v]


# ---------------------------------------------------------------------------
# Excepción interna
# ---------------------------------------------------------------------------

class AnalisisError(Exception):
    def __init__(self, codigo: str, detalle: str):
        self.codigo = codigo
        self.detalle = detalle
        super().__init__(f"{codigo}: {detalle}")


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------

def _build_user_prompt(narracion: str) -> str:
    return _USER_TEMPLATE.format(
        tipos_memorial=TIPOS_MEMORIAL,
        tipos_accion=TIPOS_ACCION,
        materias=MATERIAS,
        instancias=INSTANCIAS,
        narracion=narracion,
    )


def _strip_markdown_fence(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _parse_json(raw: str) -> Optional[dict]:
    try:
        return json.loads(_strip_markdown_fence(raw))
    except (json.JSONDecodeError, ValueError):
        return None


def calcular_confianza(analisis: AnalisisCaso) -> float:
    """Recalcula confianza_global desde el backend; no se confía en el valor del LLM."""
    if len(analisis.inventario) == 0:
        return 0.0

    score = 1.0

    if len(analisis.inventario) < 2:
        score -= 0.3

    if len(analisis.partes) == 0:
        score -= 0.4

    if not analisis.pretension or len(analisis.pretension.strip()) < 10:
        score -= 0.3

    if analisis.clasificacion.confianza < 0.6:
        score -= 0.2

    if analisis.clasificacion.tipo_accion == "otro":
        score -= 0.1

    criticas = {"input_insuficiente", "pretension_no_identificada"}
    if any(a in criticas for a in analisis.advertencias):
        score -= 0.3

    return max(0.0, min(1.0, score))


def _validar_catalogo(analisis: AnalisisCaso) -> None:
    cls = analisis.clasificacion
    if cls.tipo_memorial not in TIPOS_MEMORIAL:
        raise ValueError(f"tipo_memorial inválido: '{cls.tipo_memorial}'")
    if cls.tipo_accion not in TIPOS_ACCION:
        raise ValueError(f"tipo_accion inválido: '{cls.tipo_accion}'")


def _validar_adicional(analisis: AnalisisCaso) -> None:
    ids = [h.id for h in analisis.inventario]
    if ids != list(range(1, len(ids) + 1)):
        raise ValueError("inventario: ids deben ser únicos y secuenciales desde 1")

    activas = {"demandante", "querellante"}
    if analisis.inventario and not any(p.rol in activas for p in analisis.partes):
        raise ValueError("partes: debe existir al menos una parte con rol 'demandante' o 'querellante'")

    palabras = len(analisis.resumen_caso_query.split())
    if not (15 <= palabras <= 60):
        raise ValueError(
            f"resumen_caso_query: debe tener entre 15 y 60 palabras (tiene {palabras})"
        )

    kw = analisis.keywords_legales
    if not (3 <= len(kw) <= 10):
        raise ValueError(f"keywords_legales: debe tener entre 3 y 10 elementos (tiene {len(kw)})")
    if any(k != k.lower() for k in kw):
        raise ValueError("keywords_legales: todos los términos deben estar en minúsculas")

    cls = analisis.clasificacion
    for campo_prop, campo in [
        ("tipo_memorial_propuesto", "tipo_memorial"),
        ("tipo_accion_propuesto", "tipo_accion"),
    ]:
        propuesto = getattr(cls, campo_prop)
        valor = getattr(cls, campo)
        if valor != "otro" and propuesto is not None:
            raise ValueError(f"{campo_prop} debe ser null cuando {campo} != 'otro'")
        if propuesto is not None and not _SNAKE_CASE_RE.match(propuesto):
            raise ValueError(f"{campo_prop}: debe ser snake_case (^[a-z][a-z0-9_]{{2,39}}$)")


async def _registrar_propuestas(analisis: AnalisisCaso, db: AsyncSession) -> None:
    cls = analisis.clasificacion
    propuestas = []
    if cls.tipo_memorial == "otro" and cls.tipo_memorial_propuesto:
        propuestas.append(("tipo_memorial", cls.tipo_memorial_propuesto))
    if cls.tipo_accion == "otro" and cls.tipo_accion_propuesto:
        propuestas.append(("tipo_accion", cls.tipo_accion_propuesto))

    if not propuestas:
        return

    for campo, propuesto in propuestas:
        await db.execute(
            text("""
                INSERT INTO catalogo_propuestas (campo, valor_propuesto, frecuencia, doc_ids)
                VALUES (:campo, :propuesto, 1, ARRAY[]::uuid[])
                ON CONFLICT (campo, valor_propuesto) DO UPDATE
                    SET frecuencia = catalogo_propuestas.frecuencia + 1
                WHERE catalogo_propuestas.estado = 'pendiente'
            """),
            {"campo": campo, "propuesto": propuesto},
        )
    await db.commit()


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

async def analizar_hechos(narracion: str, db: AsyncSession) -> AnalisisCaso:
    user_prompt = _build_user_prompt(narracion)

    # Llamar al LLM — un reintento si el JSON no es válido
    raw = await llm_client.completar(
        system=_SYSTEM_PROMPT,
        user=user_prompt,
        max_tokens=4096,
        temperature=0.1,
    )
    data = _parse_json(raw)

    if data is None:
        raw = await llm_client.completar(
            system=_SYSTEM_PROMPT,
            user=user_prompt,
            max_tokens=4096,
            temperature=0.1,
        )
        data = _parse_json(raw)
        if data is None:
            raise AnalisisError("ERROR_PARSING", "El LLM no devolvió JSON válido tras reintento")

    # Validar con Pydantic
    try:
        analisis = AnalisisCaso(**data)
    except Exception as e:
        raise AnalisisError("ERROR_VALIDACION", str(e))

    # Validar catálogos
    try:
        _validar_catalogo(analisis)
    except ValueError as e:
        raise AnalisisError("ERROR_CATALOGO", str(e))

    # Validaciones adicionales de schema
    try:
        _validar_adicional(analisis)
    except ValueError as e:
        raise AnalisisError("ERROR_VALIDACION", str(e))

    # Recalcular confianza desde el backend (sobrescribir lo que devolvió el LLM)
    analisis.confianza_global = calcular_confianza(analisis)

    if analisis.confianza_global < 0.6:
        raise AnalisisError(
            "ERROR_CONFIANZA_BAJA",
            f"confianza_global={analisis.confianza_global:.2f}",
        )

    # Registrar propuestas de catálogo si el LLM usó "otro"
    await _registrar_propuestas(analisis, db)

    return analisis
