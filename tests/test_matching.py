from datetime import date

from alfred.matching import any_match, date_patterns, extract_times


def test_extract_times():
    assert 930 in extract_times("It's at 3:30 PM.")
    assert 930 in extract_times("15:30")
    assert {210, 930} <= extract_times("at 3:30")
    assert 930 not in extract_times("It's at 3:30 AM")
    assert 900 in extract_times("3pm") and 900 in extract_times("3 p.m.")
    assert 810 in extract_times("2026-10-08T13:30")
    assert 720 in extract_times("around noon")
    assert 810 not in extract_times("It's at 11:30")


def test_date_patterns():
    friday = date(2026, 10, 9)
    for text in ["Friday", "fri", "Oct 9", "October 9th", "9 October", "10/9", "2026-10-09"]:
        assert any_match(date_patterns(friday), text), text
    for text in ["Thursday", "Oct 19", "10/19", "2026-10-19", "freeday"]:
        assert not any_match(date_patterns(friday), text), text
