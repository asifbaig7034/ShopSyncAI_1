from dataclasses import dataclass
import os


def boolean(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes"}


@dataclass
class Settings:
    storage: str = "memory"
    demo_mode: bool = True
    business_id: str = "demo-business"
    dashboard_api_token: str = "demo-owner-token"
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_database: str = "shopsync"
    llm_provider: str = "demo"
    openai_api_key: str = ""
    openai_model: str = ""
    channel: str = "demo"
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_graph_version: str = ""
    order_service_url: str = ""
    orchestrator_url: str = ""
    integration_token: str = ""
    delivery_policy: str = ""
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    customer_agent_enabled: bool = True
    whatsapp_connected: bool = True

    @classmethod
    def from_env(cls):
        from dotenv import load_dotenv
        load_dotenv()
        values = {}
        for field in cls.__dataclass_fields__:
            if field in {"demo_mode", "customer_agent_enabled", "whatsapp_connected"}:
                values[field] = boolean(field.upper(), True)
            else:
                values[field] = os.getenv(field.upper(), getattr(cls(), field))
        return cls(**values)

    def validate(self):
        if self.storage not in {"memory", "mongo"}:
            raise ValueError("STORAGE must be memory or mongo")
        if self.llm_provider not in {"demo", "openai"}:
            raise ValueError("LLM_PROVIDER must be demo or openai")
        if self.channel not in {"demo", "whatsapp"}:
            raise ValueError("CHANNEL must be demo or whatsapp")
        if not self.business_id or not self.dashboard_api_token:
            raise ValueError("BUSINESS_ID and DASHBOARD_API_TOKEN are required")
        if not self.demo_mode:
            if self.dashboard_api_token == "demo-owner-token" or len(self.dashboard_api_token) < 24:
                raise ValueError("Live mode requires a non-demo DASHBOARD_API_TOKEN of at least 24 characters")
            if self.storage != "mongo":
                raise ValueError("Live mode requires persistent STORAGE=mongo")
            if self.llm_provider != "openai":
                raise ValueError("Live mode requires a configured LLM_PROVIDER=openai")
        if self.demo_mode and self.channel == "whatsapp":
            raise ValueError("Demo mode cannot send real WhatsApp messages")
        if self.llm_provider == "openai" and not (self.openai_api_key and self.openai_model):
            raise ValueError("OPENAI_API_KEY and OPENAI_MODEL are required for openai")
        if self.channel == "whatsapp":
            for name in ["whatsapp_access_token", "whatsapp_phone_number_id", "whatsapp_app_secret", "whatsapp_verify_token", "whatsapp_graph_version"]:
                if not getattr(self, name):
                    raise ValueError(f"{name.upper()} is required for WhatsApp")

