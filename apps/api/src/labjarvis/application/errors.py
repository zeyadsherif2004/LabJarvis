"""Stable application-boundary errors shared by focused workflow services."""


class ApplicationError(RuntimeError):
    """A use case could not proceed; ``code`` is safe for an API boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
