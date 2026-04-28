from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://laravel:secret@postgres:5432/expedientes_juridicos"
    storage_path: str = "/storage/private/documentos"

    class Config:
        env_file = ".env"


settings = Settings()
