from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from proxy_agent.core.config import Settings
from proxy_agent.main import create_app


def _parse_sse_events(body: str) -> list[dict | None]:
    events: list[dict | None] = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        for line in block.split("\n"):
            if line.startswith("data: "):
                payload = line[6:].strip()
                if payload == "[DONE]":
                    events.append(None)
                else:
                    events.append(json.loads(payload))
    return events


def test_chat_completions_stream_transcript_multi_turn(client: TestClient) -> None:
    """Streaming must receive full transcript in argv (echo) so multi-turn context is preserved."""
    res = client.post(
        "/v1/chat/completions",
        json={
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "mid"},
                {"role": "user", "content": "last"},
            ],
            "stream": True,
        },
    )
    assert res.status_code == 200
    events = _parse_sse_events(res.text)
    combined = "".join(
        e["choices"][0]["delta"].get("content") or ""
        for e in events
        if e is not None and "choices" in e
    )
    assert "first" in combined and "mid" in combined and "last" in combined
    non_done = [e for e in events if e is not None]
    assert non_done[-1].get("usage") is not None
    assert non_done[-1]["usage"]["total_tokens"] >= 2


def test_chat_completions_stream_sse_echo(client: TestClient) -> None:
    res = client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    )
    assert res.status_code == 200
    assert "text/event-stream" in res.headers.get("content-type", "")
    events = _parse_sse_events(res.text)
    assert events[-1] is None
    assert events[0]["object"] == "chat.completion.chunk"
    assert events[0]["choices"][0]["delta"].get("role") == "assistant"
    contents = [
        e["choices"][0]["delta"].get("content")
        for e in events
        if e is not None and "choices" in e
    ]
    assert any(c and "hello" in c for c in contents if c)
    non_done = [e for e in events if e is not None]
    assert non_done[-1]["choices"][0].get("finish_reason") == "stop"


def test_chat_completions_stream_multiple_chunks() -> None:
    script = Path(__file__).resolve().parent / "fixtures" / "stream_lines.py"
    settings = Settings(
        agent_command=sys.executable,
        agent_args_standard_template=f"{script} {{prompt}}",
        agent_args_stream_template=f"{script} {{prompt}}",
        agent_stream_protocol="passthrough",
        agent_timeout_sec=30.0,
        default_model="stream-test",
    )
    app = create_app(settings)
    with TestClient(app) as c:
        res = c.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "x"}], "stream": True},
        )
    assert res.status_code == 200
    events = [e for e in _parse_sse_events(res.text) if e is not None]
    content_parts = [
        e["choices"][0]["delta"].get("content")
        for e in events
        if e.get("choices") and e["choices"][0]["delta"].get("content")
    ]
    combined = "".join(c or "" for c in content_parts)
    assert "chunk-a" in combined and "chunk-b" in combined


def test_chat_completions_stream_timeout_yields_error_chunk() -> None:
    settings = Settings(
        agent_command="sleep",
        agent_args_standard_template="{prompt}",
        agent_args_stream_template="{prompt}",
        agent_stream_protocol="passthrough",
        agent_timeout_sec=0.2,
        default_model="t",
        agent_messages_format="last_user_only",
    )
    app = create_app(settings)
    with TestClient(app) as c:
        res = c.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "5"}], "stream": True},
        )
    assert res.status_code == 200
    assert "timed out" in res.text


def test_chat_completions_simulated_agent_multiline_reply() -> None:
    """End-to-end: subprocess prints multi-line stdout like a real agent CLI."""
    script = Path(__file__).resolve().parent / "fixtures" / "fake_agent.py"
    settings = Settings(
        agent_command=sys.executable,
        agent_args_standard_template=f"{script} {{prompt}}",
        agent_args_stream_template=f"{script} {{prompt}}",
        agent_stream_protocol="passthrough",
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
    assert data["choices"][0]["message"]["content"] == "User:\nhello"
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


def test_chat_completions_no_user_message(client: TestClient) -> None:
    res = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "assistant", "content": "only"}]},
    )
    assert res.status_code == 400
    assert "user message" in res.json()["error"]["message"].lower()


def test_transcript_multi_turn_echoed_in_prompt(client: TestClient) -> None:
    res = client.post(
        "/v1/chat/completions",
        json={
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "mid"},
                {"role": "user", "content": "last"},
            ],
        },
    )
    assert res.status_code == 200
    out = res.json()["choices"][0]["message"]["content"]
    assert "first" in out and "mid" in out and "last" in out


def test_chat_completions_cli_failure(echo_settings) -> None:
    bad = echo_settings.model_copy(
        update={
            "agent_command": "/bin/false",
            "agent_args_standard_template": "{prompt}",
            "agent_args_stream_template": "{prompt}",
        }
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


def test_chat_completions_stream_cursor_ndjson_forwards_plain_text_lines() -> None:
    """Non-JSON stdout lines still become delta.content (mixed / non-Cursor CLIs)."""
    script = Path(__file__).resolve().parent / "fixtures" / "print_plain_line.sh"
    settings = Settings(
        agent_command="bash",
        agent_args_standard_template=f"{script} {{prompt}}",
        agent_args_stream_template=f"{script} {{prompt}}",
        agent_stream_protocol="cursor_ndjson",
        agent_timeout_sec=30.0,
        default_model="ndjson-plain",
    )
    app = create_app(settings)
    with TestClient(app) as c:
        res = c.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "x"}], "stream": True},
        )
    assert res.status_code == 200
    events = [e for e in _parse_sse_events(res.text) if e is not None]
    parts = [
        e["choices"][0]["delta"].get("content")
        for e in events
        if e.get("choices") and e["choices"][0]["delta"].get("content")
    ]
    assert "plain-line" in "".join(parts)


def test_chat_completions_stream_cursor_ndjson_assistant_deltas() -> None:
    script = Path(__file__).resolve().parent / "fixtures" / "fake_cursor_stream_json.py"
    settings = Settings(
        agent_command=sys.executable,
        agent_args_standard_template=f"{script} {{prompt}}",
        agent_args_stream_template=f"{script} {{prompt}}",
        agent_stream_protocol="cursor_ndjson",
        agent_timeout_sec=30.0,
        default_model="ndjson-test",
    )
    app = create_app(settings)
    with TestClient(app) as c:
        res = c.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "x"}], "stream": True},
        )
    assert res.status_code == 200
    events = [e for e in _parse_sse_events(res.text) if e is not None]
    parts = [
        e["choices"][0]["delta"].get("content")
        for e in events
        if e.get("choices") and e["choices"][0]["delta"].get("content")
    ]
    assert "".join(parts) == "alphabeta"


def test_chat_completions_standard_json_result_field() -> None:
    script = Path(__file__).resolve().parent / "fixtures" / "print_json_result.py"
    settings = Settings(
        agent_command=sys.executable,
        agent_args_standard_template=f"{script} {{prompt}}",
        agent_args_stream_template=f"{script} {{prompt}}",
        agent_stream_protocol="passthrough",
        agent_standard_output_format="json",
        agent_timeout_sec=30.0,
        default_model="json-standard",
    )
    app = create_app(settings)
    with TestClient(app) as c:
        res = c.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "ignored"}]},
        )
    assert res.status_code == 200
    assert res.json()["choices"][0]["message"]["content"] == "standard-json-body"
