"""FR-12 — the Typer CLI.

``formcoach.cli:main`` is the console-script entry point declared in
``pyproject.toml``.
"""

from formcoach.cli.app import app, main

__all__ = ["app", "main"]
