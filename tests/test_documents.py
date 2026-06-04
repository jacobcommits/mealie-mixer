import pytest

import extract


def test_is_document():
    assert extract.is_document("recipe.pdf")
    assert extract.is_document("notes.MD")          # case-insensitive
    assert extract.is_document("card.txt")
    assert extract.is_document("mail.eml")
    assert not extract.is_document("photo.jpg")
    assert not extract.is_document("shot.png")
    assert not extract.is_document("")


def test_file_to_text_plain_and_markdown():
    assert extract.file_to_text("a.txt", b"hello world") == "hello world"
    assert extract.file_to_text("a.md", b"# Title\n- one\n") == "# Title\n- one\n"


def test_file_to_text_eml_subject_and_body():
    raw = (
        b"From: a@b.com\r\n"
        b"To: c@d.com\r\n"
        b"Subject: Grandma's Soup\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Boil water. Add noodles.\r\n"
    )
    out = extract.file_to_text("mail.eml", raw)
    assert "Subject: Grandma's Soup" in out
    assert "Boil water. Add noodles." in out


def test_pdf_garbage_raises_clear_error():
    # not a real PDF → a clear ValueError (not a raw pypdf traceback)
    with pytest.raises(ValueError):
        extract.file_to_text("bad.pdf", b"this is definitely not a pdf")
