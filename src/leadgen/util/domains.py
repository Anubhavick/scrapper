"""Domain normalisation.

Every dedupe path in the system goes through normalise_domain() — see
PROJECT.md § Data model. It must turn any of: a bare domain, a full URL
with scheme/path/query, a mixed-case host, a host with a trailing dot,
or an internationalised domain, into the same canonical ASCII form, and
it must reject input with no extractable host.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def normalise_domain(value: str) -> str:
    """Return the canonical registrable-domain form of `value`.

    - lowercases
    - strips scheme, path, query, fragment, port, userinfo
    - strips a leading "www." label (but no other subdomain)
    - strips a trailing dot
    - converts internationalised domains to their ASCII (punycode) form

    Raises ValueError if no valid host can be extracted.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"cannot normalise empty input: {value!r}")

    candidate = value.strip()
    if "://" not in candidate:
        candidate = "//" + candidate.lstrip("/")

    host = urlsplit(candidate).hostname
    if not host:
        raise ValueError(f"could not extract a host from {value!r}")

    host = host.strip(".")
    if not host:
        raise ValueError(f"empty host in {value!r}")

    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"invalid domain {value!r}") from exc

    if host.startswith("www."):
        host = host[len("www."):]

    labels = host.split(".")
    if len(labels) < 2 or any(not _LABEL_RE.match(label) for label in labels):
        raise ValueError(f"not a valid domain: {value!r}")

    return host
