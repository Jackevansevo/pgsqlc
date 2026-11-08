"""The single error type raised for every user-facing failure."""

from pathlib import Path


class PgsqlcError(Exception):
    """A generation failure, optionally located at a file and line."""

    def __init__(
        self, message: str, *, path: Path | None = None, line: int | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.path = path
        self.line = line

    def __str__(self) -> str:
        if self.path is None:
            return self.message
        if self.line is None:
            return f"{self.path}: {self.message}"
        return f"{self.path}:{self.line}: {self.message}"
