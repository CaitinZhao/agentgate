import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv():
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()


@dataclass
class Settings:
    llm_base_url: str = os.environ.get("LLM_BASE_URL", "")
    llm_api_key: str = os.environ.get("LLM_API_KEY", "")
    llm_model: str = os.environ.get("LLM_MODEL", "GLM5.3-Flash")
    llm_provider: str = os.environ.get("LLM_PROVIDER", "api")  # api | mock
    otlp_endpoint: str = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    app_port: int = int(os.environ.get("APP_PORT", "8100"))
    agent_id: str = os.environ.get("AGENT_ID", "fin-agent-01")
    component_versions: str = os.environ.get("COMPONENT_VERSIONS", "bank-report-collector@0.1.0")
