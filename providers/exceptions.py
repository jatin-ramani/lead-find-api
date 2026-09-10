"""
Provider-level exceptions.

Its own module because `providers.geoapify` imports `services.geocoder`, so
the two cannot share a type defined in either of them without a cycle.
"""

from typing import Optional


class GeoapifyError(RuntimeError):
    """
    Geoapify could not be reached, or answered with an error.

    A distinct type so callers can tell "the upstream provider is unavailable"
    — which the client should retry later — apart from "this service has a
    bug", which it should not. Without it every failure is a bare `Exception`
    and the two are indistinguishable.
    """

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_text: Optional[str] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text

