from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    supabase_jwt_secret: str = "super-secret-jwt-token-with-at-least-32-characters-long"
    openai_api_key: str = ""
    confidence_threshold: float = 0.7
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    llm_model: str = "gpt-4o-mini"
    auth_disabled: bool = False


settings = Settings()
