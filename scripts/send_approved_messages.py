"""The orchestration loop: reads `messages` where status='approved' and
sends each one through a real Gmail account, respecting the 90-600s
randomised gap and re-checked suppression/cap state (see
`src/leadgen/db/orchestration.py` and `src/leadgen/send/orchestrator.py`
for the actual logic -- this script is a thin CLI wrapper).

**Defaults to a genuinely read-only preview** (`preview_approved_messages()`):
no advisory lock, no reservation, no write of any kind -- just how many
approved messages exist per mailbox and how many of them the cap would
actually let through right now.

**--dry-run** runs the *real* loop -- sleeps, fresh suppression/cap
checks, the advisory-lock reservation -- with only the Gmail API call
faked. That reservation is a real Postgres write: every eligible message
really does flip to `status='sent'`, permanently, with no email ever
sent. Fine, even necessary, for exercising the loop against disposable
test data (see docs/12); pointed at a real approved campaign it would
silently consume those messages with nothing to show for it and no way
back except manual DB surgery. Never run --dry-run against a real
campaign expecting a safe look -- use the no-flag preview for that.

**--live** is what actually sends real email to real business owners;
it requires typing back a confirmation phrase first, on top of the flag
itself, because this is exactly the kind of external, hard-to-reverse
action that should never happen by a slipped default or a copy-pasted
command.

Usage:
    uv run python scripts/send_approved_messages.py                  # safe preview, no writes
    uv run python scripts/send_approved_messages.py --dry-run        # full loop, fake Gmail, REAL reservation writes -- disposable data only
    uv run python scripts/send_approved_messages.py --live           # the real thing
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import httpx
from dotenv import load_dotenv

from leadgen.db.orchestration import preview_approved_messages, run_approved_messages
from leadgen.db.session import session_scope
from leadgen.send.crypto import TokenCipher

CONFIRMATION_PHRASE = "send real email"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run the full loop with a faked Gmail call, but real reservation "
            "writes (messages really flip to 'sent'). Disposable test data only "
            "-- see the module docstring. Without --dry-run or --live, runs a "
            "genuinely read-only preview instead."
        ),
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="Actually call the Gmail API and send real email.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Log at DEBUG instead of INFO.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.live:
        print(
            "\n*** --live: this will send real email to real business owners "
            "through a real Gmail account. ***\n"
        )
        typed = input(f"Type '{CONFIRMATION_PHRASE}' to continue, anything else to abort: ")
        if typed.strip() != CONFIRMATION_PHRASE:
            raise SystemExit("Confirmation phrase did not match -- aborting, nothing sent.")
    elif args.dry_run:
        print(
            "\n*** --dry-run: no Gmail call, but eligible messages WILL be "
            "permanently marked 'sent' in Postgres with no email ever sent. "
            "Disposable test data only -- see the module docstring. ***\n"
        )

    load_dotenv()

    if not args.dry_run and not args.live:
        with session_scope() as session:
            summary = preview_approved_messages(session)
        if not summary:
            print("No approved messages with an active mailbox were found. Nothing to do.")
            return
        print("Preview only -- no writes, no Gmail calls. Pass --dry-run or --live to actually run the loop.\n")
        for row in summary:
            print(
                f"  mailbox {row['mailbox_id']}: {row['queued']} queued, "
                f"{row['sent_today']}/{row['daily_cap']} sent today, "
                f"{row['would_send_now']} would send now, "
                f"{row['would_be_cap_blocked']} would be cap-blocked"
            )
        return

    client_id = os.environ["GOOGLE_OAUTH_CLIENT_ID"]
    client_secret = os.environ["GOOGLE_OAUTH_CLIENT_SECRET"]
    cipher = TokenCipher(os.environ["TOKEN_ENCRYPTION_KEY"])

    with session_scope() as session, httpx.Client(timeout=30.0) as client:
        results = run_approved_messages(
            session,
            client=client,
            client_id=client_id,
            client_secret=client_secret,
            cipher=cipher,
            dry_run=not args.live,
        )

    if not results:
        print("No approved messages with an active mailbox were found. Nothing to do.")
        return

    by_outcome: dict[str, int] = {}
    for result in results:
        by_outcome[result.outcome] = by_outcome.get(result.outcome, 0) + 1
    print(f"\n{'DRY RUN ' if not args.live else ''}Done: {dict(by_outcome)}")
    for result in results:
        if result.outcome != "sent":
            print(f"  {result.outcome}: message {result.job.message_id} -- {result.detail}")


if __name__ == "__main__":
    sys.exit(main())
