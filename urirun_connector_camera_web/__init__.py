# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.

"""webcam:// connector — host a LAN browser/mobile camera service feeding the camera pipeline."""

from .core import (
    WEBCAM,
    captures,
    connector_manifest,
    main,
    start,
    status,
    stop,
    urirun_bindings,
)

__all__ = [
    "WEBCAM",
    "captures",
    "connector_manifest",
    "main",
    "start",
    "status",
    "stop",
    "urirun_bindings",
]
