from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redis_host: str = "redis"
    redis_port: int = 6379

    internal_api_key: str = "change-me-internal-dev-key"
    presence_ttl_seconds: int = 90


settings = Settings()
