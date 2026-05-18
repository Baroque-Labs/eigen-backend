"""Tiny dotenv shim: load .env with override=True so file values beat any
stale shell-exported env. Imported from entry points (main.py, worker.py,
scripts) — NOT from library modules — so tests can set their own env in
conftest without being clobbered.

Set EIGEN_DISABLE_DOTENV=1 to skip loading entirely (used by tests).
"""
import os


def load() -> None:
    if os.environ.get("EIGEN_DISABLE_DOTENV") == "1":
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(override=True)
