"""Typed errors whose messages are safe to show to the user."""


class PipelineError(Exception):
    """A failure the user should see (bad file, missing key, API error...).

    `code` is machine-readable; `message` is shown in the UI as-is.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
