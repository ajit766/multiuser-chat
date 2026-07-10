from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    jwt_secret: str = "change-me-to-a-long-random-string"
    jwt_algorithm: str = "HS256"

    internal_api_key: str = "change-me-internal-dev-key"

    presence_service_url: str = "http://presence-service:8000"
    message_service_url: str = "http://message-service:8000"


settings = Settings()
