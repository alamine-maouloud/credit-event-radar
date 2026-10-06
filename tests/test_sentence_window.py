"""sentence-window-1.1: a line break inside a wrapped sentence (HTML filings that wrap
their paragraphs) no longer ends the sentence window. The normalised text and its hashes
do not change; only the windows the extractors and the validator reason on."""

from __future__ import annotations

from radar.extract.spans import SENTENCE_WINDOW_VERSION, iter_sentences

WRAPPED = (
    "Historically, the Company has relied principally on liquidity generated from operating activities to fund the Company’s day-\n"
    "to-day operations and service its debt. The Company has a history of net losses and\n"
    "expects to continue to incur additional losses in the future.\n"
    "Outlook 2026\n"
    "The Volkswagen Group expects sales revenue to grow.\n"
    "Net liquidity at Sep. 30\n"
    "| | 31,008 | 32,829 |\n"
    "the cash anticipated to be generated from ongoing operations are not\n"
    "expected to be sufficient to generate adequate liquidity to meet the obligations over the next twelve months."
)


def test_version_is_recorded():
    assert SENTENCE_WINDOW_VERSION == "sentence-window-1.1"


def test_wrapped_lines_form_one_window_with_exact_offsets():
    sentences = iter_sentences(WRAPPED)
    texts = [s.text for s in sentences]
    first = texts[0]
    assert first.startswith("Historically") and first.endswith("service its debt.")
    assert "day-\nto-day" in first  # the newline stays in the text, offsets stay exact
    assert WRAPPED[sentences[0].start : sentences[0].end] == first
    assert any(
        t.startswith("The Company has a history") and t.endswith("in the future.") for t in texts
    )
    last = texts[-1]
    assert last.startswith("the cash anticipated") and "are not\nexpected" in last
    assert WRAPPED[sentences[-1].start : sentences[-1].end] == last


def test_headings_and_table_rows_keep_their_own_window():
    texts = [s.text for s in iter_sentences(WRAPPED)]
    assert "Outlook 2026" in texts  # next line starts with a capital: not a continuation
    assert "The Volkswagen Group expects sales revenue to grow." in texts
    # the table row keeps its own window and is never merged with the line above it
    assert any(t.startswith("| | 31,008") for t in texts)
    assert not any("31,008" in t and "liquidity" in t for t in texts)


def test_terminal_punctuation_still_ends_a_window():
    text = "Liquidity remained solid.\nand the outlook is unchanged."
    assert [s.text for s in iter_sentences(text)] == [
        "Liquidity remained solid.",
        "and the outlook is unchanged.",
    ]
