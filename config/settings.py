from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Anthropic
    anthropic_api_key: str

    # Aman DB
    aman_db_host: str
    aman_db_port: int = 3306
    aman_db_user: str
    aman_db_password: str
    aman_db_name: str

    # Webhook
    webhook_secret: str

    # Email
    gmail_client_id: str
    gmail_client_secret: str
    gmail_refresh_token: str

    class Config:
        env_file = ".env"

settings = Settings()
