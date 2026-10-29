"""Request-scoped markers used to keep remote calls away from host project files."""

from contextvars import ContextVar

REMOTE_REQUEST: ContextVar[bool] = ContextVar("skillscout_remote_request", default=False)
