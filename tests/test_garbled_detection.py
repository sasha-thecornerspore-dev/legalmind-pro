"""Corrupt text layers must be OCR'd, not indexed as noise."""
import pytest

from ingest_engine import looks_garbled


def test_real_prose_is_not_garbled():
    assert not looks_garbled(
        "Plaintiff filed a notice of pendency against the subject property "
        "on the twelfth of March, and served the defendant by certified mail.")


def test_broken_font_encoding_is_garbled():
    """Verbatim extraction from a scanned exhibit with a broken font map.

    A broken ToUnicode map makes PyMuPDF emit raw glyph indices, which land
    as control characters. 12.2% alphabetic once whitespace is discounted.
    """
    assert looks_garbled(
        '\x00\x01\x02\x03\x04\x05\x02\x06\x08\x08\x01\x03\x03\x0e\x0f\x0f'
        '\x10\x11\x11\x12\x08\x13\x14\x05\x15\x16\x17\x03\x04\x05\x08\x18'
        '\x19\x16\x05\x04\x1a\x02\x1b\x00\x16\x03\x17\x05\x08\x11\x19\x15'
        '\x04\x03\x05\x08\x08!\x11"#!\x08%&\x0e\'\x08(()*\x0e+,)-\x0f./(01'
        '\x082/30\'\x0821\x0845\x05\x17\x1a\x036\x05\x1b(()*\x0e+4789:791')


def test_short_text_is_left_to_the_empty_page_branch():
    # Under the char floor: that is the scanned-page trigger's job, and
    # calling it garbled would double-count it.
    assert not looks_garbled("Page 3 of 12")
    assert not looks_garbled("")


def test_numeric_tables_are_not_garbled():
    """A real risk: financial exhibits are digit-heavy but legitimate."""
    txt = ("Loan No. 38305   Principal 412,500.00   Interest 6.125%   "
           "Payment 2,506.44   Escrow 738.19   Due 04/01/2010   "
           "Balance 398,221.07   Late Fee 125.28   Total 401,591.98")
    assert not looks_garbled(txt), "digit-heavy financial text must survive"


def test_whitespace_padded_court_form_is_not_garbled():
    """Regression: court forms are padded, and the padding is not evidence.

    A representative chunk was 166 characters of which only 40 were
    non-blank. Counting the padding gave 19% alphabetic and would have
    caused the page to be skipped instead of indexed.
    """
    padded = "Issue 1. Supporting Facts and Argument:" + " " * 90 + "000071" + "\n" * 30
    assert not looks_garbled(padded)


def test_bates_stamped_page_is_not_garbled():
    txt = ("EXHIBIT G" + " " * 40 + "SMITH 000412\n" * 3 +
           "Assignment of Deed of Trust recorded in Liber 12345 folio 678")
    assert not looks_garbled(txt)


def test_transcript_word_index_is_not_garbled():
    """Regression: numeric density is not corruption.

    A transcript's back-of-book index is only 27% alphabetic but entirely
    legitimate. An alphabetic-ratio test flagged 48 chunks of the hearing
    transcript, which -- since unreadable pages are skipped -- would have
    silently deleted them.
    """
    idx = ("WORD INDEX < $ > $41,000 13:18 67:12 $92,000 68:9 < 1 > 1 87:7 "
           "10 11:1 80:3 10:56 58:13 10:59 58:25 10th 47:2, 3, 6 11:38 59:1 "
           "11th 52:19 12:36 86:5 13th 79:13, 20 14-211 4:4, 6 24:9 31:12")
    assert not looks_garbled(idx)


def test_payment_history_table_is_not_garbled():
    """Numeric evidence must survive: this is exactly what a note history is."""
    tbl = ("04/01/2010 2,506.44 738.19 398,221.07 04/02/2010 2,506.44 "
           "738.19 397,982.11 05/01/2010 2,506.44 738.19 397,742.02 "
           "06/01/2010 2,506.44 738.19 397,500.80")
    assert not looks_garbled(tbl)


def test_control_characters_are_the_signal():
    """Raw glyph indices land as control chars; no real page has them."""
    assert looks_garbled("".join(chr(i % 26) for i in range(120)))
