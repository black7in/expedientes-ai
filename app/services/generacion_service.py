import re
from typing import List, Literal, Optional

from bs4 import BeautifulSoup
from pydantic import BaseModel

from .analisis_service import AnalisisCaso
from .recuperacion_service import RecuperacionResult
from . import llm_client

_SYSTEM_BASE = """Eres un redactor jurídico boliviano. Tu trabajo es producir el texto de un \
memorial siguiendo estrictamente el contexto proporcionado.

Recibirás:
- INVENTARIO: hechos verificables del caso (única fuente de hechos).
- PARTES: sujetos involucrados con sus roles.
- PRETENSIÓN: lo que pide el usuario.
- MOLDE: memorial anonimizado de referencia. ÚSALO PARA: estructura, tono, \
fórmulas rituales, orden de secciones. NO COPIES sus hechos ni sus datos.
- ARTÍCULOS LEGALES: únicas normas que puedes citar.
- JURISPRUDENCIA (opcional): análisis ya digerido para integrar en fundamentos.

REGLAS ABSOLUTAS:

1. HECHOS SOLO DEL INVENTARIO. No agregues, no infieras, no completes con suposiciones.
2. Si necesitas un dato que no está, escribe inline: \
<mark class="verificar">[VERIFICAR: descripción]</mark>
3. CITA SOLO los artículos de los bloques "DERECHO SUSTANTIVO" y "DERECHO PROCESAL". \
Si falta uno necesario, escribe: <mark class="verificar">[VERIFICAR_FUNDAMENTO: tema]</mark>
4. RESPETA EL FORMATO DEL MOLDE: si es estructurado (PRIMERO, SEGUNDO...), \
también lo es tu salida. Si es corrido, también corrido.
5. NO INFLES. La extensión debe ser proporcional al inventario real. No \
repitas ideas, no uses prosa decorativa, no agregues antecedentes fabricados.
6. NO RECOMIENDES ESTRATEGIA. No propongas medidas que el usuario no pidió.
7. SALIDA EN HTML SEMÁNTICO MÍNIMO: usa <h1>, <h2>, <p>, <strong>, <mark>. \
Sin estilos inline, sin clases extra, sin <div>.

SOBRE LOS FUNDAMENTOS DE DERECHO:

No reutilices los fundamentos del molde. El molde solo te muestra el FORMATO \
de esta sección, no su contenido.

Para cada artículo legal relevante del bloque DERECHO SUSTANTIVO o PROCESAL:
1. Explica qué establece la norma.
2. Conéctala explícitamente con un hecho concreto del inventario.
3. Razona por qué ese hecho encuadra en ese artículo.

Estructura cada fundamento como: norma → hecho del caso → subsunción.

Ejemplo del razonamiento esperado (no del formato):
"El Art. 450 CC define el mutuo como [...]. En el presente caso, conforme \
al HECHO PRIMERO, el demandante entregó [MONTO] al demandado, configurándose \
[...]. Por tanto, resulta aplicable [...]."

No te limites a citar el número de artículo. Desarrolla la argumentación.

Devuelve EXCLUSIVAMENTE el HTML del memorial. Sin explicaciones antes o \
después. Sin bloques de código markdown."""

_SYSTEM_SIN_MOLDE = """Eres un redactor jurídico boliviano. Tu trabajo es producir el texto de un \
memorial siguiendo estrictamente el contexto proporcionado.

Recibirás:
- INVENTARIO: hechos verificables del caso (única fuente de hechos).
- PARTES: sujetos involucrados con sus roles.
- PRETENSIÓN: lo que pide el usuario.
- ARTÍCULOS LEGALES: únicas normas que puedes citar.
- JURISPRUDENCIA (opcional): análisis ya digerido para integrar en fundamentos.

No hay molde de referencia. Usa la estructura estándar de un memorial boliviano \
para este tipo de acción.

REGLAS ABSOLUTAS:

1. HECHOS SOLO DEL INVENTARIO. No agregues, no infieras, no completes con suposiciones.
2. Si necesitas un dato que no está, escribe inline: \
<mark class="verificar">[VERIFICAR: descripción]</mark>
3. CITA SOLO los artículos de los bloques "DERECHO SUSTANTIVO" y "DERECHO PROCESAL". \
Si falta uno necesario, escribe: <mark class="verificar">[VERIFICAR_FUNDAMENTO: tema]</mark>
4. NO INFLES. La extensión debe ser proporcional al inventario real.
5. NO RECOMIENDES ESTRATEGIA. No propongas medidas que el usuario no pidió.
6. SALIDA EN HTML SEMÁNTICO MÍNIMO: usa <h1>, <h2>, <p>, <strong>, <mark>. \
Sin estilos inline, sin clases extra, sin <div>.

SOBRE LOS FUNDAMENTOS DE DERECHO:

Para cada artículo legal relevante del bloque DERECHO SUSTANTIVO o PROCESAL:
1. Explica qué establece la norma.
2. Conéctala explícitamente con un hecho concreto del inventario.
3. Razona por qué ese hecho encuadra en ese artículo.

Estructura cada fundamento como: norma → hecho del caso → subsunción.

No te limites a citar el número de artículo. Desarrolla la argumentación.

Devuelve EXCLUSIVAMENTE el HTML del memorial. Sin explicaciones antes o \
después. Sin bloques de código markdown."""

_USER_TEMPLATE = """Genera un memorial de tipo "{tipo_memorial}" para una acción de "{tipo_accion}".

FORMATO REQUERIDO: {formato}
{instruccion_formato}

═══════════════════════════════════════════════════════════════════
INVENTARIO DE HECHOS (única fuente de hechos):
═══════════════════════════════════════════════════════════════════
{inventario}

═══════════════════════════════════════════════════════════════════
PARTES:
═══════════════════════════════════════════════════════════════════
{partes}

═══════════════════════════════════════════════════════════════════
PRETENSIÓN DEL USUARIO:
═══════════════════════════════════════════════════════════════════
{pretension}
{bloque_molde}
═══════════════════════════════════════════════════════════════════
DERECHO SUSTANTIVO — derechos y obligaciones aplicables al caso:
═══════════════════════════════════════════════════════════════════
{articulos_sustantivos}

═══════════════════════════════════════════════════════════════════
DERECHO PROCESAL — requisitos formales del documento, plazos y procedimiento:
═══════════════════════════════════════════════════════════════════
{articulos_procesales}
{bloque_jurisprudencia}
Genera el memorial completo en HTML."""


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

class Validacion(BaseModel):
    tipo: str
    detalle: str
    severidad: Literal["info", "warning"]


class GeneracionRequest(BaseModel):
    analisis: AnalisisCaso
    recuperacion: RecuperacionResult


class GeneracionResult(BaseModel):
    documento_html: str
    formato_usado: str
    molde_usado_id: Optional[str] = None
    advertencias: List[str] = []
    validaciones: List[Validacion] = []


class GeneracionError(Exception):
    def __init__(self, codigo: str, detalle: str):
        self.codigo = codigo
        self.detalle = detalle
        super().__init__(f"{codigo}: {detalle}")


# ---------------------------------------------------------------------------
# Formateadores de input
# ---------------------------------------------------------------------------

def _formatear_inventario(analisis: AnalisisCaso) -> str:
    lineas = []
    for h in analisis.inventario:
        fecha = f", {h.fecha}" if h.fecha else ""
        lineas.append(f"HECHO {h.id} ({h.relevancia} relevancia{fecha}):")
        lineas.append(f"  {h.contenido}")
        lineas.append("")
    return "\n".join(lineas).strip()


def _formatear_partes(analisis: AnalisisCaso) -> str:
    lineas = []
    for p in analisis.partes:
        nombre = p.nombre or "[VERIFICAR: nombre]"
        lineas.append(f"{p.rol.upper()} ({p.tipo.replace('_', ' ')}):")
        lineas.append(f"  Nombre: {nombre}")
        if p.datos_conocidos:
            lineas.append(f"  Datos conocidos: {', '.join(p.datos_conocidos)}")
        if p.datos_faltantes:
            lineas.append(f"  Datos faltantes: {', '.join(p.datos_faltantes)}")
        lineas.append("")
    return "\n".join(lineas).strip()


def _formatear_articulos(recuperacion: RecuperacionResult) -> str:
    if not recuperacion.leyes:
        return "(No hay artículos disponibles — usa marcas [VERIFICAR_FUNDAMENTO] donde sea necesario)"
    lineas = []
    for a in recuperacion.leyes:
        titulo = f" - {a.titulo_articulo}" if a.titulo_articulo else ""
        lineas.append(f"[{a.ley}, Art. {a.numero_articulo}{titulo}]")
        lineas.append(a.chunk_texto)
        lineas.append("")
    return "\n".join(lineas).strip()


def _formatear_articulos_procesales(recuperacion: RecuperacionResult) -> str:
    if not recuperacion.leyes_procesales:
        return "(No hay artículos procesales disponibles)"
    lineas = []
    for a in recuperacion.leyes_procesales:
        titulo = f" - {a.titulo_articulo}" if a.titulo_articulo else ""
        lineas.append(f"[{a.ley}, Art. {a.numero_articulo}{titulo}]")
        lineas.append(a.chunk_texto)
        lineas.append("")
    return "\n".join(lineas).strip()


def _bloque_molde(recuperacion: RecuperacionResult) -> str:
    if not recuperacion.molde:
        return ""
    return (
        "\n═══════════════════════════════════════════════════════════════════\n"
        "MOLDE DE REFERENCIA (solo para estructura, tono y forma):\n"
        "═══════════════════════════════════════════════════════════════════\n"
        f"{recuperacion.molde.contenido}\n"
    )


def _bloque_jurisprudencia(recuperacion: RecuperacionResult) -> str:
    if not recuperacion.jurisprudencia:
        return ""
    return (
        "\n═══════════════════════════════════════════════════════════════════\n"
        "JURISPRUDENCIA RELEVANTE:\n"
        "═══════════════════════════════════════════════════════════════════\n"
        f"{recuperacion.jurisprudencia}\n"
    )


def _instruccion_formato(formato: str) -> str:
    if formato == "estructurado":
        return "(usa numeración PRIMERO.-, SEGUNDO.-, etc.)"
    if formato == "corrido":
        return "(prosa corrida, sin numeración de secciones)"
    return "(formato mixto: encabezados + prosa)"


# ---------------------------------------------------------------------------
# Limpieza del output
# ---------------------------------------------------------------------------

def _limpiar_html(raw: str) -> str:
    raw = raw.strip()
    # Quitar bloque markdown si el LLM lo envolvió
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:html)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    # Truncar cualquier texto antes del primer tag HTML
    match = re.search(r"<[a-zA-Z]", raw)
    if match and match.start() > 0:
        raw = raw[match.start():]
    return raw.strip()


def _es_html_valido(html: str) -> bool:
    try:
        soup = BeautifulSoup(html, "html.parser")
        return bool(soup.find())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Validaciones post-generación
# ---------------------------------------------------------------------------

def _validar_output(
    documento_html: str,
    recuperacion: RecuperacionResult,
) -> tuple[list[Validacion], list[str]]:
    validaciones: list[Validacion] = []
    advertencias: list[str] = []

    # Artículos citados vs disponibles
    patron_art = re.compile(r"Art(?:ículo|\.)\s*(\d+)", re.IGNORECASE)
    citados = set(patron_art.findall(documento_html))
    disponibles = {a.numero_articulo for a in recuperacion.leyes}
    for art in citados:
        if art not in disponibles:
            validaciones.append(Validacion(
                tipo="articulo_no_encontrado",
                detalle=f"Art. {art} citado pero no está en el contexto recuperado",
                severidad="warning",
            ))

    # Marcas VERIFICAR
    marcas = re.findall(r"\[VERIFICAR[^\]]*\]", documento_html)
    if marcas:
        advertencias.append(f"documento_contiene_{len(marcas)}_verificaciones_pendientes")

    # Longitud razonable
    palabras = len(documento_html.split())
    if palabras < 200:
        advertencias.append("documento_muy_corto")
    elif palabras > 5000:
        advertencias.append("documento_muy_largo")

    return validaciones, advertencias


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

async def generar(req: GeneracionRequest) -> GeneracionResult:
    analisis = req.analisis
    recuperacion = req.recuperacion
    cls = analisis.clasificacion

    formato = recuperacion.molde.formato if recuperacion.molde else "estructurado"
    system = _SYSTEM_BASE if recuperacion.molde else _SYSTEM_SIN_MOLDE

    user_prompt = _USER_TEMPLATE.format(
        tipo_memorial=cls.tipo_memorial,
        tipo_accion=cls.tipo_accion,
        formato=formato,
        instruccion_formato=_instruccion_formato(formato),
        inventario=_formatear_inventario(analisis),
        partes=_formatear_partes(analisis),
        pretension=analisis.pretension,
        bloque_molde=_bloque_molde(recuperacion),
        articulos_sustantivos=_formatear_articulos(recuperacion),
        articulos_procesales=_formatear_articulos_procesales(recuperacion),
        bloque_jurisprudencia=_bloque_jurisprudencia(recuperacion),
    )

    # Llamada al LLM — reintento si el output está vacío o no es HTML válido
    documento_html = ""
    for intento in range(2):
        raw = await llm_client.completar(
            system=system,
            user=user_prompt,
            max_tokens=8192,
            temperature=0.3,
        )
        html = _limpiar_html(raw)
        if not html:
            if intento == 1:
                raise GeneracionError("ERROR_GENERACION_VACIA", "El LLM devolvió respuesta vacía tras reintento")
            continue
        if not _es_html_valido(html):
            if intento == 1:
                raise GeneracionError("ERROR_HTML_INVALIDO", "El output no es HTML parseable tras reintento")
            continue
        documento_html = html
        break

    validaciones, advertencias = _validar_output(documento_html, recuperacion)

    return GeneracionResult(
        documento_html=documento_html,
        formato_usado=formato,
        molde_usado_id=recuperacion.molde.doc_id if recuperacion.molde else None,
        advertencias=advertencias,
        validaciones=validaciones,
    )
