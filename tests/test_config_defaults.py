from __future__ import annotations

from proxy_agent.app import Settings


def test_agent_sse_comment_interval_default() -> None:
    assert Settings.model_fields["agent_sse_comment_interval_sec"].default == 15.0


def test_agent_stream_check_client_disconnect_default_off() -> None:
    assert Settings.model_fields["agent_stream_check_client_disconnect"].default is False
