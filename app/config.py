from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://laravel:secret@postgres:5432/expedientes_juridicos"
    storage_path: str = "/storage/documentos"
    openai_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    transformers_cache: str = "/app/.cache/huggingface"
    ner_model_path: str = "/app/modelo_ner"

    class Config:
        env_file = ".env"


settings = Settings()
