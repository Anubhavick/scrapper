from leadgen.api.nav import pagination_bar


def test_pagination_bar_empty_when_everything_fits_on_one_page() -> None:
    assert pagination_bar(1, total=10, base_url="/runs", page_size=25) == ""


def test_pagination_bar_empty_when_total_is_zero() -> None:
    assert pagination_bar(1, total=0, base_url="/runs", page_size=25) == ""


def test_pagination_bar_shows_page_count() -> None:
    html = pagination_bar(2, total=60, base_url="/runs", page_size=25)
    assert "Page 2 of 3" in html
    assert "60 total" in html


def test_pagination_bar_first_page_has_no_prev_link() -> None:
    html = pagination_bar(1, total=60, base_url="/runs", page_size=25)
    assert '<a href="/runs?page=0">' not in html
    assert "&larr; Prev</span>" in html  # disabled, not a link
    assert 'href="/runs?page=2"' in html  # next still a real link


def test_pagination_bar_last_page_has_no_next_link() -> None:
    html = pagination_bar(3, total=60, base_url="/runs", page_size=25)
    assert 'href="/runs?page=4"' not in html
    assert "Next &rarr;</span>" in html  # disabled, not a link
    assert 'href="/runs?page=2"' in html  # prev still a real link


def test_pagination_bar_middle_page_has_both_links() -> None:
    html = pagination_bar(2, total=60, base_url="/runs", page_size=25)
    assert 'href="/runs?page=1"' in html
    assert 'href="/runs?page=3"' in html


def test_pagination_bar_preserves_extra_query_params() -> None:
    html = pagination_bar(1, total=60, base_url="/runs", extra_params={"target_name": "dentists-austin-tx"}, page_size=25)
    assert "target_name=dentists-austin-tx" in html
    assert "page=2" in html


def test_pagination_bar_rounds_up_partial_last_page() -> None:
    html = pagination_bar(1, total=26, base_url="/runs", page_size=25)
    assert "Page 1 of 2" in html
