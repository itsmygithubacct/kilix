#!/usr/bin/env python3
"""Report whether Kilix can use a real GPU-backed private Wayland host."""

from __future__ import annotations

import json
import sys

from kilix_sdk.gpu_host import discover_runtime, probe_runtime


def main() -> int:
    runtime = discover_runtime()
    if runtime is None:
        result = {
            "available": False,
            "reason": "GPU host runtime is not installed",
            "install": "scripts/install-gpu-host.sh",
        }
    else:
        probe = probe_runtime(runtime)
        result = {
            "available": probe.available,
            "reason": probe.reason,
            "renderer": probe.renderer,
            "render_node": probe.render_node,
            "dmabuf": probe.dmabuf,
            "pbo": probe.pbo,
            "runtime": str(runtime.root),
        }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["available"] else 1


if __name__ == "__main__":
    sys.exit(main())
