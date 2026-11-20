from research_mcp.citations.verifier import diff_citations, extract_citekeys


def test_extract_bracketed_and_bare_citekeys():
    text = "[@smith2020] and [@doe2021; @roe2022] plus @jones2023."
    assert extract_citekeys(text) == ["smith2020", "doe2021", "roe2022", "jones2023"]


def test_bare_citekey_strips_trailing_sentence_punctuation():
    assert extract_citekeys("@jones2023.") == ["jones2023"]


def test_suppress_author_prefix_is_still_a_citation():
    assert extract_citekeys("[-@smith2020]") == ["smith2020"]


def test_email_and_handle_are_not_citations():
    assert extract_citekeys("mail me at foo@bar.com") == []
    # `@alice` after whitespace is a genuine bare citation (the `'s` stops it).
    assert extract_citekeys("see @alice's work") == ["alice"]


def test_diff_citations_separates_missing_found_and_uncited():
    referenced = ["smith2020", "fake9999"]
    library = {"smith2020", "doe2021"}
    missing, found, unreferenced = diff_citations(referenced, library)
    assert missing == ["fake9999"]
    assert found == ["smith2020"]
    assert unreferenced == ["doe2021"]
