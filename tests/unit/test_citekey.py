import re

from research_mcp.citations.bibtex import generate_citekey


def test_generate_citekey_uses_first_author_year_and_first_title_word():
    assert (
        generate_citekey(["Ashish Vaswani"], 2017, "Attention Is All You Need")
        == "vaswani2017attention"
    )


def test_generate_citekey_normalizes_unicode_and_punctuation():
    key = generate_citekey(["Élodie O'Neil-Smith"], 2024, "Über-Graph Networks!")
    assert key == "oneilsmith2024uber"
    assert re.fullmatch(r"[a-z0-9]+", key)


def test_generate_citekey_uses_unknown_and_zero_year_when_missing():
    key = generate_citekey([], None, "A Useful Paper")
    assert key == "unknown0000a"


def test_generate_citekey_adds_letter_suffix_on_collision():
    base = "vaswani2017attention"
    assert generate_citekey(["Ashish Vaswani"], 2017, "Attention", {base}) == f"{base}a"
    assert generate_citekey(["Ashish Vaswani"], 2017, "Attention", {base, f"{base}a"}) == f"{base}b"
