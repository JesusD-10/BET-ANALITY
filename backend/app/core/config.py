from pathlib import Path

from pydantic import ValidationInfo, field_validator
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
    # puede contrastar dos en paralelo dentro del mismo presupuesto total.
    ai_enabled: bool = True

    # El modo gratuito excluye proveedores con cobro por token incluso si se
    # configuró accidentalmente una clave. Requiere opt-in explícito.
    ai_allow_paid_providers: bool = False

    ai_provider_timeout_seconds: int = 4

    ai_total_timeout_seconds: int = 5

    ai_max_provider_attempts: int = 3

    # xAI / Grok
    xai_api_key: str = ""

    xai_base_url: str = "https://api.x.ai/v1"

    xai_model: str = "grok-4.3"

    # DeepSeek
    deepseek_api_key: str = ""

    deepseek_base_url: str = "https://api.deepseek.com"

    deepseek_model: str = "deepseek-v4-flash"

    # Cerebras Inference
    cerebras_api_key: str = ""

    cerebras_base_url: str = "https://api.cerebras.ai/v1"

    cerebras_model: str = "gpt-oss-120b"

    # GitHub Models cerró su servicio de inferencia el 30-07-2026. Se
    # conservan estos campos solo para diagnosticar configuraciones antiguas;
    # el orquestador nunca enviará tráfico a ese endpoint retirado.
    github_models_token: str = ""

    github_models_base_url: str = "https://models.github.ai/inference"

    github_models_model: str = "retired"

    # OpenRouter: el router `openrouter/free` limita el tráfico a modelos sin
    # costo, sujeto a su disponibilidad y cuotas diarias.
    openrouter_api_key: str = ""

    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    openrouter_model: str = "openrouter/free"

    openrouter_site_url: str = "https://bet-anality-1.onrender.com"


    # API-SPORTS / API-Football
    sports_data_provider: str = "api-football"

    api_football_key: str = ""

    api_football_base_url: str = "https://v3.football.api-sports.io"

    api_football_is_rapidapi: bool = False

    api_football_timeout_seconds: int = 3


    # Football Data API
    football_data_api_token: str = ""

    football_data_base_url: str = "https://api.football-data.org/v4"

    football_data_timeout_seconds: int = 2

    @field_validator(
        "ai_provider_timeout_seconds",
        "ai_total_timeout_seconds",
        "api_football_timeout_seconds",
        "football_data_timeout_seconds",
        mode="before",
    )
    @classmethod
    def clamp_external_timeout(cls, value: object, info: ValidationInfo) -> int:
        """Keep every external call inside the interactive 10-second budget."""
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            parsed = 5
        if info.field_name == "ai_provider_timeout_seconds":
            maximum = 5
        elif info.field_name == "ai_total_timeout_seconds":
            maximum = 5
        elif info.field_name == "api_football_timeout_seconds":
            # API-SPORTS can be slightly slower from Render. Detail calls run
            # concurrently, so three seconds still fits the browser budget.
            maximum = 3
        else:
            maximum = 2
        return max(1, min(parsed, maximum))

    @field_validator("ai_max_provider_attempts", mode="before")
    @classmethod
    def clamp_provider_attempts(cls, value: object) -> int:
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            parsed = 3
        return max(1, min(parsed, 5))

    @field_validator(
        "xai_api_key",
        "deepseek_api_key",
        "cerebras_api_key",
        "github_models_token",
        "openrouter_api_key",
        "api_football_key",
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
            "your_football_data_token_here",
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
