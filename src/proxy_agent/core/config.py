from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    agent_command: str = "agent"
    agent_args_template: str = "-p {prompt}"
    agent_cwd: Path | None = None
    agent_timeout_sec: float = 300.0
    api_key: str | None = None
    default_model: str = "proxy-agent"


@lru_cache
def get_settings() -> Settings:
    return Settings()
