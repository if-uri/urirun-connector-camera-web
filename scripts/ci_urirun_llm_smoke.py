#!/usr/bin/env python3

from __future__ import annotations

import os
import sys

from urirun_llm_runtime import Executor
from urirun_connector_camera_web.core import urirun_bindings


SMOKE_URI = "webcam://host/doctor/query/report"
CONNECTOR_ID = "webcam"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def _route_value(response: dict) -> dict:
    result = response.get("result")
    value = result.get("value", result) if isinstance(result, dict) else result
    if not isinstance(value, dict):
        fail(f"Response does not contain a dict result value: {response!r}")
    return value


def main() -> None:
    expected_routes = set(urirun_bindings().get("bindings", {}).keys())
    if SMOKE_URI not in expected_routes:
        fail(f"{SMOKE_URI} is missing from connector bindings")

    node_url = os.environ.get("URIRUN_NODE_URL", "http://127.0.0.1:18765")
    executor = Executor(node_url)

    health = executor.health()
    if not isinstance(health, dict):
        fail(f"/health returned non-dict response: {health!r}")

    routes = set(executor.routes())
    if SMOKE_URI not in routes:
        fail(f"{SMOKE_URI} is missing from /routes. Routes: {sorted(routes)!r}")

    unexpected = sorted(route for route in routes if route not in expected_routes)
    if unexpected:
        fail(f"Unexpected routes outside current connector bindings found: {unexpected!r}")

    response = executor.execute(SMOKE_URI, {})
    if not isinstance(response, dict):
        fail(f"Executor returned non-dict response: {response!r}")
    if response.get("ok") is not True:
        fail(f"Executor returned failed response: {response!r}")

    value = _route_value(response)
    if value.get("ok") is not True:
        fail(f"Smoke route returned failed response: {response!r}")
    if value.get("connector") != CONNECTOR_ID:
        fail(f"Smoke route returned wrong connector: {response!r}")

    print("OK: urirun-llm-runtime -> urirun node -> connector smoke test passed")


if __name__ == "__main__":
    main()
