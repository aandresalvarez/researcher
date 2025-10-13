#!/usr/bin/env python3
"""Send a sanity request to the local UAMM agent service."""

import json
import os
import sys
from typing import Any, Dict

import httpx


def main() -> int:
    base_url = os.getenv("UAMM_API_URL", "http://127.0.0.1:8000")
    url = f"{base_url.rstrip('/')}/agent/answer"
    payload: Dict[str, Any] = {
        "question": "Summarise the value of modular memory for analytics teams.",
        "memory_budget": 4,
        "max_refinements": 0,
    }
    headers = {"Content-Type": "application/json"}

    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=30.0)
    except Exception as exc:
        sys.stderr.write(f"Request failed: {exc}\n")
        return 1

    if resp.status_code != 200:
        sys.stderr.write(f"Unexpected status {resp.status_code}: {resp.text.strip()}\n")
        return 2

    try:
        data = resp.json()
    except json.JSONDecodeError:
        sys.stderr.write("Response was not valid JSON.\n")
        return 3

    final = data.get("final")
    stop_reason = data.get("stop_reason")
    print("Final answer:")
    print(final or "<empty>")
    print("\nDecision:", stop_reason)
    usage = data.get("usage") or {}
    if usage:
        print("Usage:", json.dumps(usage, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
