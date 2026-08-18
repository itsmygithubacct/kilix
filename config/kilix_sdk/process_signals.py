"""Signal policy for processes that own bounded runtime resources."""

import signal

TERMINATION_SIGNALS = (
    signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)


def _exit_for_cleanup(_signum, _frame):
    """Turn process termination into normal stack unwinding and cleanup."""
    raise SystemExit(0)


def install_cleanup_signal_handlers():
    for termination_signal in TERMINATION_SIGNALS:
        signal.signal(termination_signal, _exit_for_cleanup)
