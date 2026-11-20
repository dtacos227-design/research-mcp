"""Pandoc-style citation parsing and library-diff, no I/O.

Pure functions: `extract_citekeys` finds every citation key referenced in a
text using Pandoc citeproc's key grammar, and `diff_citations` splits those
keys against the project's library of known citekeys.
"""

import re

# Pandoc citeproc key characters. A key must start with a letter; the rest may
# contain alphanumerics and the punctuation citeproc allows. Our own
# generate_citekey only ever emits `[a-z][a-z0-9]*`, but this stays honest to
# Pandoc's grammar in case a draft is hand-edited.
_KEY_CHAR = r"[A-Za-z0-9_:.#$%&\-+?<>~/]"
_CITEKEY_RE = re.compile(rf"[A-Za-z]{_KEY_CHAR}*")

# A bare `@key` (no brackets) is only a citation when `@` is NOT preceded by a
# word character. This admits `@jones2023`, `[@key]`, and `[-@key]` (where `@`
# follows `[` or `-`) while excluding emails like `foo@bar.com` (where `@`
# follows `foo`). A bare key at sentence end has its trailing punctuation
# stripped so `@smith2020.` yields `smith2020`.
_BARE_CITE_RE = re.compile(r"(?<!\w)@" + rf"({_KEY_CHAR}+)")

_TRAILING_PUNCT = re.compile(r"[.,;:!?]+$")


def extract_citekeys(text: str) -> list[str]:
    """Return citation keys referenced in `text`, in first-seen order, deduped.

    Handles bracketed forms (`[@key]`, `[@k1; @k2]`), the suppress-author
    variant (`[-@key]`), and bare `@key`. A bare key at sentence end has its
    trailing punctuation stripped so `@smith2020.` yields `smith2020`.
    """
    keys: list[str] = []
    seen: set[str] = set()

    for match in _BARE_CITE_RE.finditer(text):
        key = match.group(1)
        key = _TRAILING_PUNCT.sub("", key)
        if key and key not in seen:
            seen.add(key)
            keys.append(key)

    return keys


def diff_citations(
    referenced: list[str],
    library: set[str],
) -> tuple[list[str], list[str], list[str]]:
    """Split referenced keys against the library.

    Returns `(cited_but_missing, cited_and_found, unreferenced_library)`, each
    a sorted list: the first holds keys referenced but absent from the library,
    the second keys referenced and present, the third library keys never
    referenced.
    """
    referenced_set = set(referenced)
    cited_but_missing = sorted(referenced_set - library)
    cited_and_found = sorted(referenced_set & library)
    unreferenced_library = sorted(library - referenced_set)
    return cited_but_missing, cited_and_found, unreferenced_library
