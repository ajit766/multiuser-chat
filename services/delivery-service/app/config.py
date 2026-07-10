from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    rabbitmq_user: str = "chatapp"
    rabbitmq_password: str = "chatapp"
    rabbitmq_host: str = "rabbitmq"
    rabbitmq_port: int = 5672

    presence_service_url: str = "http://presence-service:8000"
    gateway_service_url: str = "http://gateway-service:8000"
    message_service_url: str = "http://message-service:8000"

    internal_api_key: str = "change-me-internal-dev-key"


settings = Settings()
