"""Simulated agent: multi-line stdout."""
from __future__ import annotations

import sys


def main() -> None:
    prompt = " ".join(sys.argv[1:])
    print("Agent reply (simulated)")
    print(f"Echo prompt fragment: {prompt[:80]}...")
    print("Line three: done.", flush=True)


if __name__ == "__main__":
    main()
