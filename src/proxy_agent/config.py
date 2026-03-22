"""Application settings (environment / .env)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_AGENT_SUBPROCESS_STREAM_LIMIT_MIN = 1024 * 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    agent_command: str = "agent"
    agent_args_standard_template: str = "-p --output-format text {prompt}"
    agent_args_stream_template: str = (
        "-p --output-format stream-json --stream-partial-output {prompt}"
    )
    agent_stream_protocol: Literal["cursor_ndjson", "passthrough"] = "cursor_ndjson"
    agent_standard_output_format: Literal["text", "json"] = "text"
    agent_cwd: Path | None = None
    agent_timeout_sec: float = 300.0
    agent_subprocess_stream_limit: int = Field(
        default=16 * 1024 * 1024,
        ge=_AGENT_SUBPROCESS_STREAM_LIMIT_MIN,
    )
    agent_stream_stdout_chunk_size: int = 4096
    agent_use_stdbuf: bool = True
    agent_messages_format: Literal["transcript", "last_user_only"] = "transcript"
    agent_max_prompt_chars: int = 0
    agent_sse_comment_interval_sec: float = 15.0
    agent_stream_eof_process_wait_sec: float = 30.0
    agent_stream_check_client_disconnect: bool = False
    api_key: str | None = None
    default_model: str = "auto"


@lru_cache
def get_settings() -> Settings:
    return Settings()
