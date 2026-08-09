from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_GOOGLE_HOSTS = frozenset({"google.com", "www.google.com"})
_GOOGLE_REDIRECT_PATHS = frozenset({"/url", "/goto"})
_GOOGLE_TRACKING_PARAMETERS = frozenset({"srsltid"})


class InvalidDestinationUrl(ValueError):
    pass


def clean_destination_url(value: str) -> str:
    return _clean_destination_url(value, redirects_remaining=2)


def _clean_destination_url(value: str, *, redirects_remaining: int) -> str:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise InvalidDestinationUrl("destination URL is malformed") from error
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        raise InvalidDestinationUrl("destination URL must use HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise InvalidDestinationUrl("destination URL must not contain credentials")

    hostname = hostname.lower()
    if hostname in _GOOGLE_HOSTS and parsed.path in _GOOGLE_REDIRECT_PATHS:
        if redirects_remaining <= 0:
            raise InvalidDestinationUrl("Google redirect nesting is too deep")
        parameters = dict(parse_qsl(parsed.query, keep_blank_values=True))
        destination = parameters.get("q") or parameters.get("url")
        if not destination:
            raise InvalidDestinationUrl("Google redirect has no destination")
        return _clean_destination_url(
            destination,
            redirects_remaining=redirects_remaining - 1,
        )
    if hostname in _GOOGLE_HOSTS:
        raise InvalidDestinationUrl("Google navigation is not an external destination")

    retained_query = urlencode(
        [
            (name, parameter_value)
            for name, parameter_value in parse_qsl(parsed.query, keep_blank_values=True)
            if name.lower() not in _GOOGLE_TRACKING_PARAMETERS
        ],
        doseq=True,
    )
    fragment = parsed.fragment.split(":~:", 1)[0]
    default_port = (parsed.scheme.lower() == "https" and port == 443) or (
        parsed.scheme.lower() == "http" and port == 80
    )
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = host if port is None or default_port else f"{host}:{port}"
    return urlunsplit(
        (
            parsed.scheme.lower(),
            netloc,
            parsed.path or "/",
            retained_query,
            fragment,
        )
    )
