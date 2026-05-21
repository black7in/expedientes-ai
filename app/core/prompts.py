from .secciones import SECCIONES_VALIDAS

PROMPT_CLASIFICACION = """\
Sos un clasificador de documentos legales bolivianos.
Te paso los primeros caracteres de un documento. Identificá:

1. tipo_documento: demanda | sentencia | memorial | contrato | notificacion | otro
2. subtipo (solo si es demanda): ejecutiva | coactiva | ordinaria | desalojo | \
usucapion | terceria | divorcio | nulidad | otro_subtipo
3. estructura: corrido | estructurado
4. confianza: alta | media | baja
5. razon: explicación breve (1 oración)

Devolvé SOLO JSON válido, sin texto adicional, sin markdown, sin comentarios.

Ejemplo de respuesta:
{{"tipo_documento":"demanda","subtipo":"ejecutiva","estructura":"corrido",\
"confianza":"alta","razon":"Contiene pretensión de cobro con letra de cambio."}}

DOCUMENTO:
{texto}"""


def _lista_secciones(tipo_documento: str) -> str:
    secciones = SECCIONES_VALIDAS.get(tipo_documento, SECCIONES_VALIDAS["otro"])
    return " | ".join(secciones)


def prompt_parsing(tipo_documento: str, texto: str) -> str:
    secciones = _lista_secciones(tipo_documento)
    return f"""\
Sos un parser de documentos legales bolivianos. Dividí el siguiente \
{tipo_documento} en secciones según este esquema cerrado:

Secciones permitidas: {secciones}

REGLAS ESTRICTAS:
1. NO modifiques el texto original, solo lo dividís tal como aparece.
2. Si una sección no aparece en el documento, omitila.
3. Cada otrosi va como entrada separada en la lista.
4. Si algo no encaja en ninguna sección, usá "otro".
5. El campo "orden" es la posición de la sección en el documento (1, 2, 3...).

Extraé también en "metadata" si aparece explícitamente:
- monto (número) y moneda ("BOB"|"USD"|"UFV")
- departamento y ciudad
- partes: demandante y demandado (nombres completos)
- instrumento_base (letra de cambio | escritura pública | contrato | etc.)

Devolvé SOLO JSON válido, sin markdown, sin texto adicional.

Estructura exacta de respuesta:
{{
  "tipo_documento": "{tipo_documento}",
  "subtipo": null,
  "estructura": "corrido",
  "metadata": {{}},
  "secciones": [
    {{"tipo": "encabezado", "texto": "...", "orden": 1}},
    {{"tipo": "hechos", "texto": "...", "orden": 2}}
  ]
}}

DOCUMENTO:
{texto}"""


def prompt_retry(error: str, respuesta_anterior: str, tipo_documento: str) -> str:
    return f"""\
Tu respuesta anterior no era JSON válido. Error: {error}

Respuesta anterior:
{respuesta_anterior}

Corregí el JSON y devolvé SOLO el JSON válido para un {tipo_documento}, \
sin texto adicional, sin markdown."""
