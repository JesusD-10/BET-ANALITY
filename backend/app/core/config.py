from pathlib import Path

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

    openai_timeout_seconds: int = 15

    openai_max_retries: int = 2


    # API Football
    sports_data_provider: str = "api-football"

    api_football_key: str = ""

    api_football_base_url: str = "https://v3.football.api-sports.io"

    api_football_is_rapidapi: bool = False

    api_football_timeout_seconds: int = 10


    # Football Data API
    football_data_api_token: str = ""

    football_data_base_url: str = "https://api.football-data.org/v4"

    football_data_timeout_seconds: int = 5


    model_config = SettingsConfigDict(
        env_file=(
            PROJECT_ROOT / ".env",
            PROJECT_ROOT / ".env.example",
        ),
        extra="ignore",
    )


settings = Settings()