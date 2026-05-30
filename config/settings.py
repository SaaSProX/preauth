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

    # Dashboard
    dashboard_base_url: str = "http://localhost:3000"

    # Aman outbound callback (agent decision → Aman)
    aman_decisions_url: str = ""
    kpa_key: str = ""

    # CORS — comma-separated list of allowed origins (default is local dev)
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # Email (Resend)
    resend_api_key: str = ""
    resend_from_email: str = ""
 
    model_config = {"env_file": ".env", "extra": "ignore"}
 
settings = Settings()
 
