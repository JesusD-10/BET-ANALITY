from pathlib import Path
from typing import Self

from pydantic import ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):

    app_name: str = "BET ANALIZADOR API"

    environment: str = "production"

    allowed_origins: list[str] = [
        "https://bet-anality-1.onrender.com"
    ]

    next_public_api_url: str = "https://bet-anality.onrender.com/api/v1"


    # Motor multi-IA. Las consultas simples rotan un proveedor; el análisis
    # contrasta hasta cuatro proveedores en paralelo dentro de un plazo común.
    ai_enabled: bool = True

    # Puede ponerse en false para excluir DeepSeek cuando se quiera un
    # despliegue estrictamente gratuito. El consenso de cuatro requiere true.
    ai_allow_paid_providers: bool = True

    ai_provider_timeout_seconds: int = 18

    ai_total_timeout_seconds: int = 22

    ai_max_provider_attempts: int = 4

    # Groq (API compatible con Chat Completions de OpenAI)
    groq_api_key: str = ""

    groq_base_url: str = "https://api.groq.com/openai/v1"

    groq_model: str = "openai/gpt-oss-120b"

    # DeepSeek
    deepseek_api_key: str = ""

    deepseek_base_url: str = "https://api.deepseek.com"

    deepseek_model: str = "deepseek-v4-flash"

    # Cerebras Inference
    cerebras_api_key: str = ""

    cerebras_base_url: str = "https://api.cerebras.ai/v1"

    cerebras_model: str = "gpt-oss-120b"

    # OpenRouter: el router `openrouter/free` limita el tráfico a modelos sin
    # costo, sujeto a su disponibilidad y cuotas diarias.
    openrouter_api_key: str = ""

    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    openrouter_model: str = "openrouter/free"

    openrouter_site_url: str = "https://bet-anality-1.onrender.com"

    # Database Configuration (Defaults to SQLite for local development)
    database_url: str = "sqlite:///./bet_analizador.db"

    # API-SPORTS / API-Football
    sports_data_provider: str = "api-football"

    api_football_key: str = ""

    api_football_base_url: str = "https://v3.football.api-sports.io"

    api_football_is_rapidapi: bool = False

    api_football_timeout_seconds: int = 10

    # `auto` usa el contexto completo en planes con cuota amplia y conserva el
    # plan Free para H2H, forma enriquecida, bajas y cuotas. `full` fuerza todos
    # los bloques opcionales; `quota-saving` conserva siempre el perfil básico.
    api_football_enrichment_mode: str = "auto"

    api_football_optional_quota_reserve: int = 15


    # Sportmonks Football API v3
    sportmonks_api_token: str = ""

    sportmonks_base_url: str = "https://api.sportmonks.com/v3/football"

    sportmonks_timeout_seconds: int = 15


    # Football Data API
    football_data_api_token: str = ""

    football_data_base_url: str = "https://api.football-data.org/v4"

    football_data_timeout_seconds: int = 10

    # Presupuesto de la cadena de agenda completa. Los tres proveedores se
    # prueban en secuencia, pero el navegador deja un margen adicional para el
    # análisis multi-IA y la serialización de la respuesta.
    sports_data_total_timeout_seconds: int = 40

    @field_validator(
        "ai_provider_timeout_seconds",
        "ai_total_timeout_seconds",
        "api_football_timeout_seconds",
        "sportmonks_timeout_seconds",
        "football_data_timeout_seconds",
        "sports_data_total_timeout_seconds",
        mode="before",
    )
    @classmethod
    def clamp_external_timeout(cls, value: object, info: ValidationInfo) -> int:
        """Bound configurable network waits without forcing premature aborts."""
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            parsed = 5
        if info.field_name == "ai_provider_timeout_seconds":
            maximum = 25
        elif info.field_name == "ai_total_timeout_seconds":
            maximum = 30
        elif info.field_name == "api_football_timeout_seconds":
            maximum = 15
        elif info.field_name == "sportmonks_timeout_seconds":
            # Sportmonks' official examples use 30 seconds. The interactive
            # route uses a smaller configurable ceiling plus provider fallback.
            maximum = 25
        elif info.field_name == "sports_data_total_timeout_seconds":
            maximum = 60
        else:
            maximum = 15
        return max(1, min(parsed, maximum))

    @field_validator("ai_max_provider_attempts", mode="before")
    @classmethod
    def clamp_provider_attempts(cls, value: object) -> int:
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            parsed = 3
        return max(1, min(parsed, 4))

    @field_validator("api_football_enrichment_mode", mode="before")
    @classmethod
    def validate_api_football_enrichment_mode(cls, value: object) -> str:
        normalized = str(value or "auto").strip().casefold()
        if normalized not in {"auto", "full", "quota-saving"}:
            raise ValueError(
                "API_FOOTBALL_ENRICHMENT_MODE debe ser auto, full o quota-saving"
            )
        return normalized

    @field_validator("api_football_optional_quota_reserve", mode="before")
    @classmethod
    def clamp_api_football_quota_reserve(cls, value: object) -> int:
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            parsed = 15
        return max(0, min(parsed, 1000))

    @model_validator(mode="after")
    def preserve_all_sports_provider_attempts(self) -> Self:
        """Reserve enough backend time to attempt every configured adapter."""

        required = (
            self.api_football_timeout_seconds
            + self.sportmonks_timeout_seconds
            + self.football_data_timeout_seconds
        )
        self.sports_data_total_timeout_seconds = min(
            60,
            max(self.sports_data_total_timeout_seconds, required),
        )
        return self

    @field_validator("sportmonks_base_url")
    @classmethod
    def require_secure_sportmonks_url(cls, value: str) -> str:
        clean_value = value.strip().rstrip("/")
        if not clean_value.casefold().startswith("https://"):
            raise ValueError("SPORTMONKS_BASE_URL debe usar HTTPS")
        return clean_value

    @field_validator(
        "groq_api_key",
        "deepseek_api_key",
        "cerebras_api_key",
        "openrouter_api_key",
        "api_football_key",
        "sportmonks_api_token",
        "football_data_api_token",
    )
    @classmethod
    def ignore_example_credentials(cls, value: str, info: ValidationInfo) -> str:
        clean_value = value.strip()
        placeholders = {
            info.field_name.casefold(),
            f"your_{info.field_name}_here",
            f"tu_{info.field_name}_aqui",
            "your_api_football_key_here",
            "your_sportmonks_api_token_here",
            "tu_token_sportmonks",
            "tu_sportmonks_api_token_aqui",
            "your_football_data_token_here",
            "tu_token_football_data",
            "tu_football_data_token_aqui",
        }
        if clean_value.casefold() in placeholders:
            return ""
        return clean_value


    model_config = SettingsConfigDict(
        env_file=(
            PROJECT_ROOT / ".env",
            PROJECT_ROOT / ".env.development.local",
        ),
        extra="ignore",
    )


settings = Settings()
