"""The API error type, rendered as {"error": {"code", "message"}} (docs/architecture.md §3)."""


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: object) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra  # e.g. fields=[...] for 422, retry_after=... for 429
