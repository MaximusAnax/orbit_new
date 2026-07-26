import pytest

pytest_plugins = ("pytest_asyncio",)


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires running Postgres")
