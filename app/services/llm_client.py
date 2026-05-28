import anthropic
import openai as openai_sdk
from ..config import settings


async def completar(
    system: str,
    user: str,
    max_tokens: int = 8192,
    temperature: float = 0.1,
) -> str:
    """
    Llama al LLM configurado y retorna el texto de la respuesta.
    Provider se elige con LLM_PROVIDER en .env: "anthropic" | "openai"
    """
    if settings.llm_provider == "openai":
        return await _completar_openai(system, user, max_tokens, temperature)
    return await _completar_anthropic(system, user, max_tokens, temperature)


async def get_model() -> str:
    return settings.llm_model


async def _completar_anthropic(
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
) -> str:
    kwargs: dict = {"api_key": settings.anthropic_api_key}
    if settings.llm_base_url:
        kwargs["base_url"] = settings.llm_base_url

    client = anthropic.AsyncAnthropic(**kwargs)

    # Providers alternativos vía base_url (OpenCode, etc.) no siempre soportan
    # el campo `system` como parámetro separado — se fusiona en el user message.
    if settings.llm_base_url:
        merged = f"<system>\n{system}\n</system>\n\n{user}"
        create_kwargs: dict = dict(
            model=settings.llm_model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": merged}],
            temperature=temperature,
        )
    else:
        create_kwargs = dict(
            model=settings.llm_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=temperature,
        )

    msg = await client.messages.create(**create_kwargs)
    bloque = next((b for b in msg.content if hasattr(b, "text")), None)
    return bloque.text.strip() if bloque else ""


async def _completar_openai(
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
) -> str:
    kwargs: dict = {"api_key": settings.openai_api_key}
    if settings.llm_base_url:
        kwargs["base_url"] = settings.llm_base_url + "/v1"

    client = openai_sdk.AsyncOpenAI(**kwargs, timeout=90.0)
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
        # Modelos reasoning (kimi, deepseek) agotan tokens en chain-of-thought
        # sin llegar a la respuesta — subir max_tokens o acortar el documento
        finish = choice.finish_reason
        raise RuntimeError(
            f"El modelo devolvió respuesta vacía (finish_reason={finish}). "
            "El documento puede ser demasiado largo para el límite de tokens."
        )
    return content.strip()
