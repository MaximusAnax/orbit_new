"""Structured error codes shared by services, API and CLI (SCOPE.md FR-14)."""

from __future__ import annotations


class DatasweepError(Exception):
    """Base class carrying a stable machine-readable ``code``."""

    code = "error"
    http_status = 400

    def __init__(self, message: str, **detail: object) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, **self.detail}


class UnknownRunError(DatasweepError):
    code = "unknown_run"
    http_status = 404


class UnknownItemError(DatasweepError):
    """A review item id that this run does not carry.

    Distinct from :class:`UnknownRunError` so a caller can tell "no such run"
    from "no such item in this run"; it extends SCOPE.md FR-14's catalog rather
    than overloading one of its codes.
    """

    code = "unknown_item"
    http_status = 404


class ItemAlreadyDecidedError(DatasweepError):
    code = "item_already_decided"
    http_status = 409


class FileUnstableError(DatasweepError):
    code = "file_unstable"
    http_status = 409


class UnsupportedFormatError(DatasweepError):
    code = "unsupported_format"
    http_status = 415


class FileTooLargeError(DatasweepError):
    code = "file_too_large"
    http_status = 413


class MissingDependencyError(DatasweepError):
    """A live adapter was selected but its optional extra is not installed."""

    code = "missing_dependency"
    http_status = 501
