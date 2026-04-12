from ideascroller.scraper import parse_comment_count

def test_parse_plain_number():
    assert parse_comment_count("300") == 300

def test_parse_k_suffix():
    assert parse_comment_count("1.2K") == 1200

def test_parse_m_suffix():
    assert parse_comment_count("2.5M") == 2500000

def test_parse_empty():
    assert parse_comment_count("") == 0

def test_parse_invalid():
    assert parse_comment_count("abc") == 0

def test_parse_lowercase_k():
    assert parse_comment_count("1.2k") == 1200
