from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from proxy_agent.app import Settings, create_app


@pytest.fixture
def echo_settings() -> Settings:
    return Settings(
        agent_command="echo",
        agent_args_standard_template="{prompt}",
        agent_args_stream_template="{prompt}",
        agent_stream_protocol="passthrough",
        agent_timeout_sec=30.0,
        agent_sse_comment_interval_sec=0.0,
        api_key=None,
        default_model="test-model",
    )


@pytest.fixture
def client(echo_settings: Settings) -> TestClient:
    app = create_app(echo_settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def authed_settings() -> Settings:
    return Settings(
        agent_command="echo",
        agent_args_standard_template="{prompt}",
        agent_args_stream_template="{prompt}",
        agent_stream_protocol="passthrough",
        agent_timeout_sec=30.0,
        agent_sse_comment_interval_sec=0.0,
        api_key="test-secret-key",
        default_model="authed-model",
    )


@pytest.fixture
def authed_client(authed_settings: Settings) -> TestClient:
    app = create_app(authed_settings)
    with TestClient(app) as test_client:
        yield test_client
