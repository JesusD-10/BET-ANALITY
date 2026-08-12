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


    # OpenAI
    openai_api_key: str = ""

    openai_model: str = "gpt-4o-mini"

    openai_timeout_seconds: int = 5

    openai_max_retries: int = 0


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
        "openai_timeout_seconds",
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
        if info.field_name == "openai_timeout_seconds":
            maximum = 5
        elif info.field_name == "api_football_timeout_seconds":
            # API-SPORTS can be slightly slower from Render. Detail calls run
            # concurrently, so three seconds still fits the browser budget.
            maximum = 3
        else:
            maximum = 2
        return max(1, min(parsed, maximum))

    @field_validator("openai_max_retries", mode="before")
    @classmethod
    def disable_interactive_retries(cls, value: object) -> int:
        # A retry can multiply the visible wait even when each call has a timeout.
        return 0

    @field_validator("openai_api_key", "api_football_key", "football_data_api_token")
    @classmethod
    def ignore_example_credentials(cls, value: str) -> str:
        clean_value = value.strip()
        if clean_value.casefold() in {
            "openai_api_key",
            "your_api_football_key_here",
            "your_football_data_token_here",
            "tu_openai_api_key_aqui",
            "tu_football_data_token_aqui",
        }:
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
