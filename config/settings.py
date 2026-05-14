from pydantic_settings import BaseSettings
 
class Settings(BaseSettings):
    # Anthropic
    anthropic_api_key: str = ""
 
    # Aman HMO DB (MySQL)
    aman_db_host: str = ""
    aman_db_port: int = 3306
    aman_db_user: str = ""
    aman_db_password: str = ""
    aman_db_name: str = ""
 
    # Our DB (PostgreSQL)
    our_db_url: str = ""
 
    # Webhook
    webhook_secret: str = ""

    jwt_secret: str = ""

 
    # Email (Gmail OAuth)
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
 
    model_config = {"env_file": ".env", "extra": "ignore"}
 
settings = Settings()
 