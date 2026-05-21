import json
import re

from ..core.prompts import prompt_parsing, prompt_retry
from .llm_client import crear_cliente, get_model

_MAX_RETRIES = 3


async def parsear_documento(texto: str, tipo_documento: str) -> dict:
    client = crear_cliente()
    prompt = prompt_parsing(tipo_documento, texto)
    respuesta_anterior = ""
    ultimo_error = ""

    for intento in range(_MAX_RETRIES):
        contenido = prompt if intento == 0 else prompt_retry(ultimo_error, respuesta_anterior, tipo_documento)

        msg = await client.messages.create(
            model=get_model(),
            max_tokens=8192,
            messages=[{"role": "user", "content": contenido}],
        )

        bloque = next((b for b in msg.content if hasattr(b, "text")), None)
        raw = bloque.text.strip() if bloque else ""
        respuesta_anterior = raw

        try:
            resultado = _extraer_json(raw)
            _normalizar_resultado(resultado, tipo_documento)
            return resultado
        except (json.JSONDecodeError, ValueError) as e:
            ultimo_error = str(e)
            if intento == _MAX_RETRIES - 1:
                raise RuntimeError(
                    f"Parser falló {_MAX_RETRIES} intentos. Último error: {ultimo_error}"
                ) from e

    raise RuntimeError("Parser: error inesperado")


def _extraer_json(raw: str) -> dict:
    """Intenta parsear JSON directamente o extrae el primer bloque JSON del texto."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise


def _normalizar_resultado(resultado: dict, tipo_documento: str) -> None:
    """Asegura campos mínimos en la respuesta del parser."""
    resultado.setdefault("tipo_documento", tipo_documento)
    resultado.setdefault("subtipo", None)
    resultado.setdefault("estructura", "corrido")
    resultado.setdefault("metadata", {})
    resultado.setdefault("secciones", [])

    if not isinstance(resultado["secciones"], list):
        raise ValueError("El campo 'secciones' debe ser una lista.")
