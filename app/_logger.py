"""
Shared logger factory — private module (starts with _), not mapped as a route.
"""

import sys
from datetime import datetime, timezone


def create_logger(tag: str):
    """Create a logger with the given tag prefix."""

    class _Logger:
        @staticmethod
        def _ts() -> str:
            return datetime.now(timezone.utc).isoformat()

        @staticmethod
        def log(*args: object) -> None:
            try:
                print(f"[{tag}][{_Logger._ts()}]", *args, flush=True)
            except UnicodeEncodeError:
                safe_args = [
                    str(arg).encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8")
                    for arg in args
                ]
                print(f"[{tag}][{_Logger._ts()}]", *safe_args, flush=True)

        @staticmethod
        def error(*args: object) -> None:
            try:
                print(f"[{tag}][{_Logger._ts()}]", *args, file=sys.stderr, flush=True)
            except UnicodeEncodeError:
                safe_args = [
                    str(arg).encode(sys.stderr.encoding or "utf-8", errors="replace").decode(sys.stderr.encoding or "utf-8")
                    for arg in args
                ]
                print(f"[{tag}][{_Logger._ts()}]", *safe_args, file=sys.stderr, flush=True)

    return _Logger()
