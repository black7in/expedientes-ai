from ..core.secciones import SECCIONES_VALIDAS

_INTEGRIDAD_MIN = 0.85   # 85% del texto original debe estar cubierto
_MAX_CHARS_CHUNK = 1800  # ~450 tokens — margen seguro bajo el límite de 512 del E5


def validar_parse(resultado: dict, texto_original: str, tipo_documento: str) -> None:
    """
    Valida la respuesta del parser. Lanza ValueError con descripción si falla.
    Checks:
      1. Secciones dentro del esquema permitido.
      2. Cobertura de texto >= 85% del original.
    """
    secciones_permitidas = set(SECCIONES_VALIDAS.get(tipo_documento, SECCIONES_VALIDAS["otro"]))
    secciones = resultado.get("secciones", [])

    # 1. Secciones válidas
    for sec in secciones:
        tipo_sec = sec.get("tipo", "")
        if tipo_sec not in secciones_permitidas:
            raise ValueError(
                f"Sección '{tipo_sec}' no permitida para tipo '{tipo_documento}'. "
                f"Permitidas: {sorted(secciones_permitidas)}"
            )

    # 2. Integridad: el texto reconstruido debe cubrir >= 85% del original
    texto_reconstruido = " ".join(s.get("texto", "") for s in secciones)
    if len(texto_original) > 0:
        cobertura = len(texto_reconstruido) / len(texto_original)
        if cobertura < _INTEGRIDAD_MIN:
            raise ValueError(
                f"Cobertura de texto insuficiente: {cobertura:.0%} "
                f"(mínimo {_INTEGRIDAD_MIN:.0%}). El LLM probablemente resumió el contenido."
            )


def sub_chunkear(texto: str, encabezado_seccion: str) -> list[str]:
    """
    Divide un texto de sección largo en sub-chunks seguros para E5-large (<512 tokens).
    Repite el encabezado de sección en cada sub-chunk para mantener contexto.
    """
    if len(texto) <= _MAX_CHARS_CHUNK:
        return [texto]

    lineas = texto.split("\n")
    chunks: list[str] = []
    actual = encabezado_seccion + "\n" if encabezado_seccion else ""

    for linea in lineas:
        if len(actual) + len(linea) > _MAX_CHARS_CHUNK:
            if actual.strip():
                chunks.append(actual.strip())
            actual = (encabezado_seccion + "\n" if encabezado_seccion else "") + linea + "\n"
        else:
            actual += linea + "\n"

    if actual.strip():
        chunks.append(actual.strip())

    return chunks if chunks else [texto[:_MAX_CHARS_CHUNK]]
