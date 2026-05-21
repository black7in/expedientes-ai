import anthropic
from ..config import settings


def crear_cliente() -> anthropic.AsyncAnthropic:
    """Crea el cliente Anthropic apuntando al provider configurado en settings."""
    kwargs: dict = {"api_key": settings.anthropic_api_key}
    if settings.llm_base_url:
        kwargs["base_url"] = settings.llm_base_url
    return anthropic.AsyncAnthropic(**kwargs)


def get_model() -> str:
    return settings.llm_model
