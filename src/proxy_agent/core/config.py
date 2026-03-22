from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    agent_stream_stdout_chunk_size: int = 4096
    agent_use_stdbuf: bool = True
    agent_messages_format: Literal["transcript", "last_user_only"] = "transcript"
    agent_max_prompt_chars: int = 0
    agent_sse_comment_interval_sec: float = 0.0
    agent_stream_eof_process_wait_sec: float = 30.0
    api_key: str | None = None
    default_model: str = "auto"


@lru_cache
def get_settings() -> Settings:
    return Settings()
