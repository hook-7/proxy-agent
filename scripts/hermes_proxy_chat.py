#!/usr/bin/env python3
"""Small CLI helper for Hermes ↔ proxy-agent collaboration.

Usage examples:
  python3 scripts/hermes_proxy_chat.py "Summarize this repository"
  echo "Review the latest diff" | python3 scripts/hermes_proxy_chat.py --stdin
  python3 scripts/hermes_proxy_chat.py --base-url http://127.0.0.1:8088 --no-stream "Hello"

Environment variables:
  PROXY_AGENT_URL   Default base URL (default: http://127.0.0.1:8000)
  PROXY_AGENT_API_KEY  Optional Bearer token
  PROXY_AGENT_MODEL Default model (default: auto)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Iterable


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send a prompt to proxy-agent using OpenAI-style chat completions.")
    parser.add_argument("prompt", nargs="?", help="User prompt. If omitted, use --stdin or pipe text into stdin.")
    parser.add_argument("--base-url", default=os.environ.get("PROXY_AGENT_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--model", default=os.environ.get("PROXY_AGENT_MODEL", "auto"))
    parser.add_argument("--api-key", default=os.environ.get("PROXY_AGENT_API_KEY"))
    parser.add_argument("--system", help="Optional system message prepended to the chat request.")
    parser.add_argument("--no-stream", action="store_true", help="Use non-streaming mode and print the final assistant message.")
    parser.add_argument("--stdin", action="store_true", help="Read the prompt from stdin even if a positional prompt is not provided.")
    parser.add_argument("--raw-json", action="store_true", help="Print raw JSON response / SSE lines instead of extracting assistant text.")
    return parser


def _resolve_prompt(args: argparse.Namespace) -> str:
    if args.stdin:
        prompt = sys.stdin.read().strip()
        if prompt:
            return prompt
    if args.prompt:
        return args.prompt
    if not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
        if prompt:
            return prompt
    raise SystemExit("No prompt provided. Pass a positional prompt or use --stdin.")


def _build_messages(prompt: str, system_prompt: str | None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return messages


def _headers(api_key: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _post_json(url: str, payload: dict, headers: dict[str, str]) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Request failed: {exc}") from exc


def _iter_sse_lines(url: str, payload: dict, headers: dict[str, str]) -> Iterable[str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            while True:
                line = resp.readline()
                if not line:
                    break
                yield line.decode("utf-8", errors="replace").rstrip("\n")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Request failed: {exc}") from exc


def _extract_text_from_response(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    return content if isinstance(content, str) else ""


def _run_stream(url: str, payload: dict, headers: dict[str, str], raw_json: bool) -> int:
    printed = False
    for line in _iter_sse_lines(url, payload, headers):
        if not line:
            continue
        if raw_json:
            print(line)
            printed = True
            continue
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            break
        obj = json.loads(data)
        for choice in obj.get("choices", []):
            delta = choice.get("delta") or {}
            text = delta.get("content")
            if isinstance(text, str):
                print(text, end="", flush=True)
                printed = True
    if printed and not raw_json:
        print()
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    prompt = _resolve_prompt(args)
    url = args.base_url.rstrip("/") + "/v1/chat/completions"
    headers = _headers(args.api_key)
    payload = {
        "model": args.model,
        "messages": _build_messages(prompt, args.system),
        "stream": not args.no_stream,
    }

    if args.no_stream:
        response = _post_json(url, payload, headers)
        if args.raw_json:
            print(json.dumps(response, ensure_ascii=False, indent=2))
        else:
            print(_extract_text_from_response(response))
        return 0

    return _run_stream(url, payload, headers, args.raw_json)


if __name__ == "__main__":
    raise SystemExit(main())
