#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from uamm.mcp.server import run


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run UAMM MCP server (Pydantic AI MCP)"
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (if supported)")
    parser.add_argument(
        "--port", type=int, default=8765, help="Bind port (if supported)"
    )
    args = parser.parse_args()
    try:
        run(host=args.host, port=args.port)
        return 0
    except Exception as exc:
        sys.stderr.write(f"Failed to start MCP server: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
