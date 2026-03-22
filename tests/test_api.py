from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from proxy_agent.app import Settings, create_app


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


def test_chat_completions_stream_sse_echo(client: TestClient) -> None:
    res = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}], "stream": True},
    )
    assert res.status_code == 200
    events = _parse_sse_events(res.text)
    assert events[-1] is None
    contents = [
        e["choices"][0]["delta"].get("content")
        for e in events
        if e is not None and "choices" in e
    ]
    assert any(c and "hello" in c for c in contents if c)


def test_chat_completions_stream_multiple_chunks() -> None:
    script = Path(__file__).resolve().parent / "fixtures" / "stream_lines.py"
    settings = Settings(
        agent_command=sys.executable,
        agent_args_standard_template=f"{script} {{prompt}}",
        agent_args_stream_template=f"{script} {{prompt}}",
        agent_stream_protocol="passthrough",
        agent_sse_comment_interval_sec=0.0,
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
    combined = "".join(
        e["choices"][0]["delta"].get("content") or ""
        for e in _parse_sse_events(res.text)
        if e is not None and "choices" in e
    )
    assert "chunk-a" in combined and "chunk-b" in combined


def test_chat_completions_stream_timeout_yields_error_chunk() -> None:
    settings = Settings(
        agent_command="sleep",
        agent_args_standard_template="{prompt}",
        agent_args_stream_template="{prompt}",
        agent_stream_protocol="passthrough",
        agent_sse_comment_interval_sec=0.0,
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
    assert "timed out" in res.text.lower()


def test_chat_completions_echo(client: TestClient) -> None:
    res = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["choices"][0]["message"]["content"] == "User:\nhello"


def test_chat_completions_no_user_message(client: TestClient) -> None:
    res = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "assistant", "content": "only"}]},
    )
    assert res.status_code == 400


def test_list_models(client: TestClient) -> None:
    res = client.get("/v1/models")
    assert res.status_code == 200
    assert res.json()["data"][0]["id"] == "test-model"


def test_models_requires_auth_when_configured(authed_client: TestClient) -> None:
    assert authed_client.get("/v1/models").status_code == 401


def test_chat_with_invalid_bearer(authed_client: TestClient) -> None:
    res = authed_client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer wrong"},
        json={"messages": [{"role": "user", "content": "x"}]},
    )
    assert res.status_code == 401


def test_chat_completions_stream_cursor_ndjson_plain_lines() -> None:
    script = Path(__file__).resolve().parent / "fixtures" / "print_plain_line.sh"
    settings = Settings(
        agent_command="bash",
        agent_args_standard_template=f"{script} {{prompt}}",
        agent_args_stream_template=f"{script} {{prompt}}",
        agent_stream_protocol="cursor_ndjson",
        agent_sse_comment_interval_sec=0.0,
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
    combined = "".join(
        e["choices"][0]["delta"].get("content") or ""
        for e in _parse_sse_events(res.text)
        if e is not None and "choices" in e
    )
    assert "plain-line" in combined


def test_chat_completions_stream_cursor_ndjson_assistant_deltas() -> None:
    script = Path(__file__).resolve().parent / "fixtures" / "fake_cursor_stream_json.py"
    settings = Settings(
        agent_command=sys.executable,
        agent_args_standard_template=f"{script} {{prompt}}",
        agent_args_stream_template=f"{script} {{prompt}}",
        agent_stream_protocol="cursor_ndjson",
        agent_sse_comment_interval_sec=0.0,
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
    combined = "".join(
        e["choices"][0]["delta"].get("content") or ""
        for e in _parse_sse_events(res.text)
        if e is not None and "choices" in e
    )
    assert combined == "alphabeta"


def test_chat_completions_stream_cursor_ndjson_stops_on_result() -> None:
    script = Path(__file__).resolve().parent / "fixtures" / "cursor_stream_then_hang.py"
    settings = Settings(
        agent_command=sys.executable,
        agent_args_standard_template=f"{script} {{prompt}}",
        agent_args_stream_template=f"{script} {{prompt}}",
        agent_stream_protocol="cursor_ndjson",
        agent_sse_comment_interval_sec=0.0,
        agent_timeout_sec=8.0,
        agent_stream_eof_process_wait_sec=0.0,
        default_model="hang-test",
    )
    app = create_app(settings)
    with TestClient(app) as c:
        res = c.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "x"}], "stream": True},
        )
    assert res.status_code == 200
    assert "timed out" not in res.text.lower()
    assert "ok-from-hang-fix" in res.text


def test_chat_completions_stream_long_ndjson_line() -> None:
    script = Path(__file__).resolve().parent / "fixtures" / "print_long_assistant_ndjson.py"
    settings = Settings(
        agent_command=sys.executable,
        agent_args_standard_template=f"{script} {{prompt}}",
        agent_args_stream_template=f"{script} {{prompt}}",
        agent_stream_protocol="cursor_ndjson",
        agent_sse_comment_interval_sec=0.0,
        agent_timeout_sec=30.0,
        default_model="long-line",
    )
    app = create_app(settings)
    with TestClient(app) as c:
        res = c.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "x"}], "stream": True},
        )
    assert res.status_code == 200
    assert "Separator is found" not in res.text
    assert "proxy-agent stream error" not in res.text


def test_chat_completions_standard_json_result() -> None:
    script = Path(__file__).resolve().parent / "fixtures" / "print_json_result.py"
    settings = Settings(
        agent_command=sys.executable,
        agent_args_standard_template=f"{script} {{prompt}}",
        agent_args_stream_template=f"{script} {{prompt}}",
        agent_stream_protocol="passthrough",
        agent_standard_output_format="json",
        agent_sse_comment_interval_sec=0.0,
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
