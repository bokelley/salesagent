"""Unit tests for the signal_id slugify utility."""

from __future__ import annotations

from src.admin.utils.signal_id import slugify_signal_id, unique_signal_id


class TestSlugifySignalId:
    def test_ascii_lowercase_words(self):
        assert slugify_signal_id("Sports fans 18-34") == "sports_fans_18-34"

    def test_collapses_runs_of_unsafe_chars(self):
        assert slugify_signal_id("foo!!!bar   baz") == "foo_bar_baz"

    def test_strips_leading_trailing_separators(self):
        assert slugify_signal_id("__foo__") == "foo"

    def test_underscore_and_hyphen_preserved(self):
        assert slugify_signal_id("a_b-c") == "a_b-c"

    def test_empty_and_whitespace_only(self):
        assert slugify_signal_id("") == ""
        assert slugify_signal_id("   ") == ""

    def test_non_ascii_collapses_to_empty(self):
        # Non-ascii chars all get killed → empty after strip
        assert slugify_signal_id("日本語") == ""

    def test_truncates_long_input(self):
        assert len(slugify_signal_id("a" * 1000)) == 180


class TestUniqueSignalId:
    def test_returns_base_when_free(self):
        assert unique_signal_id("Sports fans", exists=lambda _: False) == "sports_fans"

    def test_appends_counter_on_collision(self):
        taken = {"sports_fans"}
        assert unique_signal_id("Sports fans", exists=taken.__contains__) == "sports_fans_2"

    def test_walks_counter_until_free(self):
        taken = {"sports_fans", "sports_fans_2", "sports_fans_3"}
        assert unique_signal_id("Sports fans", exists=taken.__contains__) == "sports_fans_4"

    def test_falls_back_when_slug_empty(self):
        # Non-ascii name → empty slug → fallback to "signal"
        assert unique_signal_id("日本語", exists=lambda _: False) == "signal"
