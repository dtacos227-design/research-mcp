import re
import unicodedata


def _ascii_words(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.findall(r"[a-z0-9]+", ascii_value)


def _letter_suffix(index: int) -> str:
    suffix = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        suffix = chr(ord("a") + remainder) + suffix
    return suffix


def generate_citekey(
    authors: list[str],
    year: int | None,
    title: str,
    existing: set[str] | None = None,
) -> str:
    author_token = authors[0].split()[-1] if authors and authors[0].split() else ""
    author = "".join(_ascii_words(author_token)) or "unknown"
    year_part = f"{year:04d}" if year is not None and 0 <= year <= 9999 else "0000"
    title_words = _ascii_words(title)
    title_word = title_words[0] if title_words else "paper"
    base = f"{author}{year_part}{title_word}"

    existing_keys = existing or set()
    if base not in existing_keys:
        return base

    suffix_index = 1
    while f"{base}{_letter_suffix(suffix_index)}" in existing_keys:
        suffix_index += 1
    return f"{base}{_letter_suffix(suffix_index)}"
