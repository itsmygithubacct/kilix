"""Internal subprocess lifecycle helpers shared by Kilix SDK owners."""

from __future__ import annotations

import os
import signal


def stop_process(
    process,
    timeout: float = 2.0,
    *,
    process_group: bool = False,
) -> None:
    """Stop one ``Popen``-compatible process and close its parent handles."""
    if process is None:
        return
    if process.poll() is None:
        try:
            if process_group:
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=timeout)
        except Exception:
            try:
                if process_group:
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except Exception:
                pass
            try:
                process.wait(timeout=1)
            except Exception:
                pass
    for handle in (
        getattr(process, "stdin", None),
        getattr(process, "stdout", None),
        getattr(process, "stderr", None),
    ):
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass


__all__ = ["stop_process"]
