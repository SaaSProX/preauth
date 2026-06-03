from pydantic import field_validator
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
    dashboard_base_url: str = "https://dashboard.saasprolabs.io"

    # Aman outbound callback (agent decision → Aman)
    aman_callback_enabled: bool = False
    aman_decisions_url: str = ""
    kpa_key: str = ""

    # CORS — comma-separated list of allowed origins.
    cors_origins: str = "https://dashboard.saasprolabs.io,http://localhost:5174,http://127.0.0.1:5174,http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000"

    # Agent control — set to false to pause automated decisions during stabilization.
    agent_enabled: bool = False

    # Email (Resend)
    resend_api_key: str = ""
    resend_from_email: str = ""

    # Google OAuth / Gmail integration
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = ""
    google_pubsub_topic_name: str = ""
    gmail_watch_label_ids: str = "INBOX"
    gmail_pubsub_verification_token: str = ""

    @field_validator(
        "aman_callback_enabled",
        "agent_enabled",
        mode="before",
    )
    @classmethod
    def _strip_bool_env(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator(
        "aman_decisions_url",
        "kpa_key",
        "cors_origins",
        "dashboard_base_url",
        "google_oauth_redirect_uri",
        "google_pubsub_topic_name",
        "gmail_watch_label_ids",
        "gmail_pubsub_verification_token",
        mode="before",
    )
    @classmethod
    def _strip_string_env(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value
 
    model_config = {"env_file": ".env", "extra": "ignore"}
 
settings = Settings()
 
