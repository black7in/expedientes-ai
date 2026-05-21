import json
import re

from ..core.prompts import PROMPT_CLASIFICACION
from .llm_client import crear_cliente, get_model


async def clasificar_documento(texto: str) -> dict:
    client = crear_cliente()

    msg = await client.messages.create(
        model=get_model(),
        max_tokens=256,
        messages=[{
            "role": "user",
            "content": PROMPT_CLASIFICACION.format(texto=texto[:2000]),
        }],
    )

    bloque = next((b for b in msg.content if hasattr(b, "text")), None)
    raw = bloque.text.strip() if bloque else ""

    try:
        resultado = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            resultado = json.loads(match.group())
        else:
            resultado = {
                "tipo_documento": "otro",
                "subtipo": None,
                "estructura": "corrido",
                "confianza": "baja",
                "razon": "No se pudo clasificar automáticamente.",
            }

    return resultado
