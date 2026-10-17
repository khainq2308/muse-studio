#!/usr/bin/env python3
"""CLI tool to probe muse2api instance health, readiness, and model capabilities."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from muse_orchestrator.providers.muse2api_adapter import Muse2ApiAdapter


async def probe(base_url: str, api_key: str | None = None) -> int:
    print(f"[*] Probing muse2api at: {base_url}")
    adapter = Muse2ApiAdapter(base_url=base_url, api_key=api_key)

    health = await adapter.health()
    print(f"[*] Health status: {health.status_state.upper()} (latency: {health.latency_ms:.1f}ms)")
    if health.details:
        print(f"[*] Details: {json.dumps(health.details)}")

    if not health.healthy:
        print("[-] Target instance is unhealthy or unreachable.")
        return 1

    try:
        models = await adapter.list_models()
        print(f"[+] Discovered {len(models)} model(s):")
        for m in models:
            print(f"    - {m.model_id} ({m.modality}) [status: {m.verification_status}]")
    except Exception as exc:
        print(f"[-] Failed to retrieve models: {exc}")
        return 1

    print("[+] Probe succeeded!")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe muse2api instance")
    parser.add_argument("--base-url", default="http://localhost:8080", help="Base URL of muse2api instance")
    parser.add_argument("--api-key", default=None, help="Optional bearer token or API key")
    args = parser.parse_args()

    exit_code = asyncio.run(probe(args.base_url, args.api_key))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
