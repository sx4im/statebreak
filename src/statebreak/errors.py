"""Package-specific exception classes for StateBreak."""


class StateBreakError(Exception):
    """Base exception for all StateBreak errors."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


class UsageError(StateBreakError):
    """Raised for invalid CLI arguments, options, or unknown commands (Exit Code 2)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=2)


class ConfigurationError(StateBreakError):
    """Raised for invalid configuration or scenario input (Exit Code 2)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=2)


class FindingRegressionError(StateBreakError):
    """Raised when scenario run detects regressions/findings under strict mode (Exit Code 1)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=1)


class InternalError(StateBreakError):
    """Raised for unexpected runner or internal faults (Exit Code 3)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=3)
