"""OpenAI-compatible proxy for a local agent CLI. Implementation lives in ``app.py``."""

from proxy_agent.app import app, create_app, run

__all__ = ["app", "create_app", "run"]
