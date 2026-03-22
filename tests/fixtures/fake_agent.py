"""Stand-in CLI: prints a multi-line reply to stdout (exit 0)."""
from __future__ import annotations

import sys


def main() -> None:
    prompt = sys.argv[1] if len(sys.argv) > 1 else ""
    print("Agent reply (simulated)")
    print("---")
    print("Echo prompt:", prompt)
    print("Line three: done.")


if __name__ == "__main__":
    main()
