"""Typer CLI (FR-13). `newsalpha.cli:main` is the console-script entry point."""

from .main import EXIT_BY_CODE, app, main

__all__ = ["EXIT_BY_CODE", "app", "main"]
