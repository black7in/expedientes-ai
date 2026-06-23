import openai
from ..config import settings


async def completar(
    system: str,
    user: str,
    max_tokens: int = 8192,
    temperature: float = 0.1,
) -> str:
    client = openai.AsyncOpenAI(api_key=settings.openai_api_key, timeout=90.0)
    resp = await client.chat.completions.create(
        model=settings.llm_model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
    )
    choice = resp.choices[0]
    content = choice.message.content
    if not content:
        raise RuntimeError(
            f"El modelo devolvió respuesta vacía (finish_reason={choice.finish_reason}). "
            "El documento puede ser demasiado largo para el límite de tokens."
        )
    return content.strip()


async def get_model() -> str:
    return settings.llm_model
