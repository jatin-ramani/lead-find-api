"""
Provider-level exceptions.

Its own module because `providers.geoapify` imports `services.geocoder`, so
the two cannot share a type defined in either of them without a cycle.
"""


class GeoapifyError(RuntimeError):
    """
    Geoapify could not be reached, or answered with an error.

    A distinct type so callers can tell "the upstream provider is unavailable"
    — which the client should retry later — apart from "this service has a
    bug", which it should not. Without it every failure is a bare `Exception`
    and the two are indistinguishable.
    """
