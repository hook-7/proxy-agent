from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from proxy_agent.core.config import Settings
from proxy_agent.main import create_app


def test_chat_completions_simulated_agent_multiline_reply() -> None:
    """End-to-end: subprocess prints multi-line stdout like a real agent CLI."""
    script = Path(__file__).resolve().parent / "fixtures" / "fake_agent.py"
    settings = Settings(
        agent_command=sys.executable,
        agent_args_template=f"{script} {{prompt}}",
        agent_timeout_sec=30.0,
        default_model="fake-agent",
    )
    app = create_app(settings)
    with TestClient(app) as c:
        res = c.post(
            "/v1/chat/completions",
            json={
                "messages": [
                    {"role": "user", "content": "What does this codebase do?"},
                ],
            },
        )
    assert res.status_code == 200
    data = res.json()
    content = data["choices"][0]["message"]["content"]
    assert "Agent reply (simulated)" in content
    assert "What does this codebase do?" in content
    assert "Line three: done." in content
    assert data["model"] == "fake-agent"
    assert data["usage"]["completion_tokens"] >= 1


def test_chat_completions_echo(client: TestClient) -> None:
    res = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert data["choices"][0]["message"]["content"] == "hello"
    assert data["model"] == "test-model"


def test_chat_completions_custom_model(client: TestClient) -> None:
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "custom",
            "messages": [{"role": "user", "content": "x"}],
        },
    )
    assert res.status_code == 200
    assert res.json()["model"] == "custom"


def test_chat_completions_stream_not_supported(client: TestClient) -> None:
    res = client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "x"}],
            "stream": True,
        },
    )
    assert res.status_code == 400
    assert res.json()["error"]["message"] == "stream=true is not supported"


def test_chat_completions_no_user_message(client: TestClient) -> None:
    res = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "assistant", "content": "only"}]},
    )
    assert res.status_code == 400
    assert "No user message" in res.json()["error"]["message"]


def test_chat_completions_cli_failure(echo_settings) -> None:
    bad = echo_settings.model_copy(
        update={"agent_command": "/bin/false", "agent_args_template": "{prompt}"}
    )
    app = create_app(bad)
    with TestClient(app) as c:
        res = c.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "x"}]},
        )
    assert res.status_code == 502
    body = res.json()
    assert body["error"]["type"] == "agent_execution_error"
    assert body["error"]["exit_code"] == 1


def test_list_models(client: TestClient) -> None:
    res = client.get("/v1/models")
    assert res.status_code == 200
    data = res.json()
    assert data["object"] == "list"
    assert len(data["data"]) == 1
    assert data["data"][0]["id"] == "test-model"


def test_models_requires_auth_when_configured(authed_client: TestClient) -> None:
    res = authed_client.get("/v1/models")
    assert res.status_code == 401


def test_models_with_valid_bearer(authed_client: TestClient) -> None:
    res = authed_client.get(
        "/v1/models",
        headers={"Authorization": "Bearer test-secret-key"},
    )
    assert res.status_code == 200
    assert res.json()["data"][0]["id"] == "authed-model"


def test_chat_with_invalid_bearer(authed_client: TestClient) -> None:
    res = authed_client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer wrong"},
        json={"messages": [{"role": "user", "content": "x"}]},
    )
    assert res.status_code == 401
