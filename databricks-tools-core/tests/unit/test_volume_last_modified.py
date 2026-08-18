"""
Unit tests for normalise_last_modified().

Regression cover for the 2026-08-10 bug: get_volume_file_metadata() called
.isoformat() unconditionally on w.files.get_metadata().last_modified, which the
Files API returns as an RFC 7231 HTTP-date STRING, not a datetime. Every call to
the get_volume_file_info MCP tool failed with:

    'str' object has no attribute 'isoformat'

These tests pin all three real-world input shapes so a future refactor can't
quietly reintroduce a type assumption.
"""

from datetime import datetime, timezone

import pytest

from databricks_tools_core.unity_catalog.volume_files import normalise_last_modified


class TestTheBugItself:
    """The exact shape that crashed, taken verbatim from a live response."""

    def test_http_date_string_does_not_raise(self):
        # This input previously raised AttributeError.
        assert normalise_last_modified("Sun, 09 Aug 2026 21:48:17 GMT") is not None

    def test_http_date_string_parses_to_correct_instant(self):
        got = normalise_last_modified("Sun, 09 Aug 2026 21:48:17 GMT")
        assert got is not None
        assert datetime.fromisoformat(got) == datetime(2026, 8, 9, 21, 48, 17, tzinfo=timezone.utc)


class TestEpochMilliseconds:
    """list_directory_contents() returns epoch MILLISECONDS as an int."""

    def test_epoch_ms_from_a_real_listing(self):
        # 1786312097000 ms is the icmsarch minutes upload, 2026-08-09 UTC.
        got = normalise_last_modified(1786312097000)
        assert got is not None
        assert datetime.fromisoformat(got) == datetime(2026, 8, 9, 21, 48, 17, tzinfo=timezone.utc)

    def test_ms_and_http_date_agree_for_the_same_instant(self):
        """The two read paths must not disagree about the same file."""
        from_listing = normalise_last_modified(1786312097000)
        from_metadata = normalise_last_modified("Sun, 09 Aug 2026 21:48:17 GMT")
        assert from_listing == from_metadata

    def test_seconds_are_not_misread_as_milliseconds(self):
        # Below the 1e11 threshold -> treated as seconds, not ms.
        got = normalise_last_modified(1786312097)
        assert got is not None
        assert datetime.fromisoformat(got).year == 2026


class TestOtherShapes:
    def test_datetime_passes_through(self):
        dt = datetime(2026, 8, 9, 21, 48, 17, tzinfo=timezone.utc)
        assert normalise_last_modified(dt) == dt.isoformat()

    def test_none_stays_none(self):
        assert normalise_last_modified(None) is None

    def test_empty_string_is_none(self):
        assert normalise_last_modified("") is None
        assert normalise_last_modified("   ") is None

    def test_iso_string_round_trips(self):
        assert normalise_last_modified("2026-08-09T21:48:17+00:00") == ("2026-08-09T21:48:17+00:00")

    def test_iso_string_with_z_suffix(self):
        got = normalise_last_modified("2026-08-09T21:48:17Z")
        assert got is not None
        assert datetime.fromisoformat(got) == datetime(2026, 8, 9, 21, 48, 17, tzinfo=timezone.utc)

    def test_bool_is_not_treated_as_epoch(self):
        # bool subclasses int; True must not become 1970-01-01.
        assert normalise_last_modified(True) == "True"


class TestNeverRaises:
    """A metadata nicety must not be able to fail the call that carries it."""

    @pytest.mark.parametrize(
        "value",
        [
            "not a date at all",
            float("nan"),
            object(),
            [1, 2, 3],
            10**30,  # out of datetime range
        ],
    )
    def test_unparseable_input_returns_something_without_raising(self, value):
        got = normalise_last_modified(value)
        assert got is None or isinstance(got, str)

    def test_unrecognised_string_is_preserved_not_dropped(self):
        assert normalise_last_modified("not a date at all") == "not a date at all"
