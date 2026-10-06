"""SEC HTML normaliser: deterministic, idempotent, keeps tables, drops hidden content."""

from __future__ import annotations

import pytest

from radar.normalize import (
    NORMALIZER_VERSION,
    SecHtmlNormalizer,
    decode_bytes,
    html_to_text,
    normalize_sec_html,
    normalize_text,
)

HTML = b"""<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html>
<html><head><title>t</title><style>p{color:red}</style><script>var x="NOPE";</script></head>
<body><!-- comment -->
<div style="display:none"><ix:header>HIDDEN</ix:header></div>
<div style="DISPLAY: none">ALSO HIDDEN</div>
<p><span>The Company</span><span>&#8217;s</span> <span>ratings</span> were as follows:</p>
<table><tr><td>Moody&#8217;s</td><td>Baa3</td><td>Stable</td></tr></table>
<p>Non\xc2\xa0breaking   spaces\tand\ttabs.</p>
<p>Zero\xe2\x80\x8bwidth.</p>
</body></html>"""


def test_hidden_and_non_content_nodes_are_dropped():
    text = normalize_sec_html(HTML)
    for forbidden in ("HIDDEN", "NOPE", "color:red", "comment", "xml version", "DOCTYPE"):
        assert forbidden not in text


def test_inline_spans_do_not_split_words():
    text = normalize_sec_html(HTML)
    assert "The Company’s ratings were as follows:" in text


def test_table_rows_keep_cells_on_one_line():
    text = normalize_sec_html(HTML)
    assert "Moody’s | Baa3 | Stable" in text.splitlines()


def test_whitespace_and_zero_width():
    text = normalize_sec_html(HTML)
    assert "Non breaking spaces and tabs." in text
    assert "Zerowidth." in text


def test_normalize_text_is_idempotent():
    once = normalize_sec_html(HTML)
    assert normalize_text(once) == once


def test_two_runs_are_identical():
    a = SecHtmlNormalizer().normalize(HTML)
    b = SecHtmlNormalizer().normalize(HTML)
    assert a == b
    assert SecHtmlNormalizer().version == NORMALIZER_VERSION


def test_no_empty_lines():
    text = normalize_sec_html(HTML)
    assert all(line.strip() for line in text.splitlines())


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("café".encode(), "café"),
        (b'<meta charset="iso-8859-1">caf\xe9', '<meta charset="iso-8859-1">café'),
        (b"caf\xe9 no charset", "café no charset"),
    ],
)
def test_decode_bytes(raw, expected):
    assert decode_bytes(raw) == expected


def test_html_to_text_cell_separator_not_at_line_end():
    text = normalize_text(html_to_text("<table><tr><td>a</td><td>b</td></tr></table>"))
    assert text == "a | b"


def test_nested_blocks():
    text = normalize_sec_html(b"<div><div><p>one</p></div><p>two</p></div>")
    assert text == "one\ntwo"
