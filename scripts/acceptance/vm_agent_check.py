from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a manually registered VM Agent host."
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--host-id", type=int, required=True)
    parser.add_argument("--api-token", default="")
    parser.add_argument("--agent-config", type=Path)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(".tmp/stage-g/vm/vm-agent-acceptance.json"),
    )
    parser.add_argument(
        "--allow-insecure-local",
        action="store_true",
        help="Allow http/ws only for loopback testing.",
    )
    return parser.parse_args()


def _is_loopback(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _config_check(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"provided": False}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    server_url = str(payload.get("server_url") or "")
    return {
        "provided": True,
        "server_scheme": urlsplit(server_url).scheme,
        "token_configured": bool(payload.get("token")),
        "environment": payload.get("environment"),
        "capabilities": payload.get("capabilities") or [],
    }


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    base_scheme = urlsplit(base_url).scheme
    if base_scheme != "https" and not (
        args.allow_insecure_local and _is_loopback(base_url)
    ):
        print(
            "Remote VM acceptance requires HTTPS/WSS. "
            "Use --allow-insecure-local only for loopback tests."
        )
        return 2

    headers = {}
    if args.api_token:
        headers["Authorization"] = f"Bearer {args.api_token}"
    report: dict[str, Any] = {
        "base_url": base_url,
        "host_id": args.host_id,
        "agent_config": _config_check(args.agent_config),
    }
    failures: list[str] = []
    try:
        with httpx.Client(
            timeout=args.timeout,
            headers=headers,
        ) as client:
            deadline = time.time() + args.timeout
            host = None
            while time.time() < deadline:
                response = client.get(f"{base_url}/api/hosts")
                response.raise_for_status()
                hosts = response.json().get("hosts") or []
                host = next(
                    (
                        item
                        for item in hosts
                        if int(item.get("host_id", -1))
                        == args.host_id
                    ),
                    None,
                )
                if host is not None:
                    break
                time.sleep(1)
            if host is None:
                failures.append("host_not_found")
                report["host"] = None
            else:
                report["host_initial"] = host
                if host.get("environment") != "vm":
                    failures.append("environment_not_vm")
                if "isolated_desktop" not in (
                    host.get("capabilities") or []
                ):
                    failures.append("isolated_desktop_missing")
                if host.get("status") not in {"online", "busy"}:
                    failures.append("host_not_online")
                first_seen = host.get("last_seen_at")
                time.sleep(2.5)
                refreshed = client.get(f"{base_url}/api/hosts")
                refreshed.raise_for_status()
                current = next(
                    (
                        item
                        for item in refreshed.json().get("hosts") or []
                        if int(item.get("host_id", -1))
                        == args.host_id
                    ),
                    None,
                )
                report["host_after_heartbeat"] = current
                if current is None:
                    failures.append("host_disappeared")
                elif current.get("last_seen_at") == first_seen:
                    failures.append("heartbeat_not_updated")
    except Exception as exc:
        failures.append(f"request_failed:{type(exc).__name__}:{exc}")

    if report["agent_config"].get("provided"):
        config = report["agent_config"]
        if config.get("environment") != "vm":
            failures.append("agent_config_environment_not_vm")
        if config.get("server_scheme") != "wss":
            failures.append("agent_config_not_wss")
        if "isolated_desktop" not in (
            config.get("capabilities") or []
        ):
            failures.append("agent_config_isolated_missing")
        if not config.get("token_configured"):
            failures.append("agent_token_missing")

    report["failures"] = failures
    report["status"] = "passed" if not failures else "failed"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"REPORT={args.report}")
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
