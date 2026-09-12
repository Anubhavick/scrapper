"""Suppression-check decision logic.

Per PROJECT.md's hard rules, this check runs before **every** send, no
exceptions, and is never gated behind a flag. `suppressions` is `(scope,
value)` — this module takes the two scopes as plain sets so the decision
itself has no DB dependency and no way to accidentally skip one scope. The
actual row fetch lives in `db/repository.py`.
"""

from __future__ import annotations

from leadgen.util.domains import normalise_domain

__all__ = ["is_suppressed"]


def is_suppressed(
    *,
    email: str,
    suppressed_emails: set[str],
    suppressed_domains: set[str],
) -> bool:
    if email.strip().lower() in suppressed_emails:
        return True

    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    try:
        normalized = normalise_domain(domain)
    except ValueError:
        return False
    return normalized in suppressed_domains
