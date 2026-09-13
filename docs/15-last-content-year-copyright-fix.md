# 15 — Fixed `last_content_year` mistaking a copyright footer for fresh content

ROADMAP.md's item 2, open since docs/07 flagged it during the 31-row
Austin dentist review: `last_content_year` regexed the *entire* crawled
page text for the largest 4-digit year found. A "© 2026 Clinic. All
rights reserved." footer — almost always auto-generated to today's year
by whatever template/CMS rendered it — reliably won that max, so a
genuinely stale site got reported as updated *this year*. Directly
affects lead quality: a profile using `stale_content_before_year`
(qualify.py) would silently never flag sites like this as stale, no
matter how old their real content actually is.

## The fix

`src/leadgen/enrich/signals.py`'s `_extract_years()` now strips any year
that appears immediately after a copyright marker (`©`, `(c)`, or the
word "copyright", case-insensitive) — including a trailing range like
`© 2015-2026` — before running the existing "largest 4-digit year"
regex over what's left:

```python
_COPYRIGHT_YEAR_RE = re.compile(
    rf"(?:©|\(c\)|copyright)[^0-9]{{0,20}}{_YEAR_RE}(?:\s*[-–—]\s*{_YEAR_RE})?",
    re.IGNORECASE,
)
```

If every year on a page turns out to be copyright-marked, `last_content_year`
is now `None` rather than a fabricated "fresh" signal — reporting *unknown*
is strictly more honest than reporting a footer year as real content
freshness. A page that never mentions any year at all is unaffected
(`None`, unchanged from before). Deliberately narrow: this targets the
specific pattern from docs/07's bug report (a marker directly attached to
a year) — it doesn't try to guess at every possible way a template might
render a copyright notice, and it doesn't touch any other signal.

## Tests

`tests/test_signals.py`: the old `test_signals_last_content_year_takes_max_across_pages`
asserted the *buggy* behavior (a footer year of 2023 beating a real
"Est. 2015" mention) — rewritten to use two genuine content years instead,
plus four new cases:

- a copyright-only page → `None`, not the footer year (the core bug)
- a real content year alongside a newer copyright-footer year → the real
  (older) year wins
- a copyright year *range* (`(c) 2015-2026`) → both ends excluded, `None`
- no year mentioned anywhere → `None`, confirming the fix doesn't regress
  the no-year case

`tests/test_crawler.py`'s integration-level fixture had the same
buggy-behavior assumption baked in (`/about` was *only* `&copy; 2022
Clinic`, asserting `last_content_year == 2022`) — updated to
`"Established 2019. &copy; 2022 Clinic"`, asserting `2019` now wins.

## Verification against real sites

Re-ran the real crawler (`crawl_business`, real `httpx` requests, the
same user agent and crawl_pages `run_pipeline.py` uses, robots.txt
respected) against four real businesses from the existing
`leads-austin-dentists.csv` run that had all shown the tell-tale
`last_content_year: 2026` (today's actual year — the smoking gun for this
bug):

| Site | Before (buggy) | After (fixed) |
|---|---|---|
| blvddentistry.com | 2026 | **2000** |
| riverydental.com | 2026 | **2017** |
| parkfieldfamilydental.com | 2026 | **None** (no non-footer year anywhere) |
| smile360atx.com | 2026 | 2026 (unchanged — see below) |

Three of four flipped from a fabricated "fresh" signal to either a real
year or an honest `None`. The fourth, smile360atx.com, still shows 2026
after the fix — inspected its raw crawled text directly and confirmed
this is *not* the copyright-footer bug: the page's homepage embeds
customer-review JSON-LD with real `"datePublished": "2026-04-28"` values
(actual review timestamps), separate from its own `©2012 - 2026 Smile
360` footer (which the fix does correctly strip). The page genuinely
did get new content in 2026 in the form of reviews, so `2026` is an
accurate signal here, not a repeat of the bug — a different, narrower
kind of noise (embedded structured-data dates unrelated to editorial
content) than what docs/07 reported, and out of scope for this fix.
Noting it here rather than silently leaving it for someone to
rediscover.

`uv run pytest`: 207/207 passing (previously 203; +5 net after rewriting
one existing test and adding four).

## Not done here, still real gaps

- The JSON-LD/structured-data review-date noise on sites like
  smile360atx.com, above — a real but distinct source of false
  "freshness," not the bug this round targeted.
- No change to how `qualify.py`/`tags.py` consume `last_content_year` —
  a profile that already opted into `stale_content_before_year` will now
  get more accurate qualification decisions automatically, with no
  config change needed.
