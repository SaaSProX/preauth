from pydantic import field_validator
from pydantic_settings import BaseSettings
 
class Settings(BaseSettings):
    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
 
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

    # Applied mode guardrails (SAA-61)
    # When applied_mode_enabled is True, decisions within guardrails are enforced.
    # Anything outside guardrails remains advisory-only.
    applied_mode_enabled: bool = False
    
    # Amount thresholds (in Naira)
    applied_max_item_amount: float = 25000.0      # Max per line item
    applied_max_pa_amount: float = 100000.0       # Max per PA total
    
    # Category allowlist (comma-separated category_ids)
    # 1=Drugs, 2=Services, 3=Labs, 4=Radiology, 5=Dental, 6=Optical, 7=Immunization, 8=Wellness
    applied_category_allowlist: str = "3,7,8"     # Labs, Immunizations, Wellness
    
    # Category denylist (always advisory, comma-separated)
    applied_category_denylist: str = "4"          # Radiology always manual
    
    # Care type denylist (always advisory, comma-separated)
    # 1=Inpatient, 2=Outpatient, 3=Antenatal, 4=Dental, 5=Optical, 6=Telemedicine, 7=Wellness
    applied_caretype_denylist: str = "1,3"        # Inpatient, Antenatal always manual
    
    # Plan denylist (comma-separated plan names, case-insensitive)
    applied_plan_denylist: str = "platinum,platinum plus,platinum+"
    
    # Confidence requirement
    applied_require_high_confidence: bool = True
    
    # Utilization threshold (pause if enrollee used > X% of annual limit)
    applied_utilization_threshold: float = 0.80  # 80%

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
        "anthropic_model",
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
 
