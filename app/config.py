from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://laravel:secret@postgres:5432/expedientes_juridicos"
    storage_path: str = "/storage/documentos"
    # LLM — elegir provider: "anthropic" (incluye OpenCode Go) | "openai"
    llm_provider: str = "anthropic"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = "claude-sonnet-4-6"
    transformers_cache: str = "/app/.cache/huggingface"

    class Config:
        env_file = ".env"


settings = Settings()
