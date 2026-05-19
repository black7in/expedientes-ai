from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://laravel:secret@postgres:5432/expedientes_juridicos"
    storage_path: str = "/storage/documentos"
    anthropic_api_key: str = ""
    transformers_cache: str = "/app/.cache/huggingface"

    class Config:
        env_file = ".env"


settings = Settings()
