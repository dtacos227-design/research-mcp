# Design: Fase 3 — Citation Manager & Anti-Hallucination Engine

**Status:** Draft, pending builder check
**Branch:** `feature/phase-3-citation-manager` (forked from `main` at the
Fase 2 merge commit)
**Spec source:** `AGENTS.md` §6.1.D (`generate_bibliography`,
`verify_citations`), §7 "Fase 3", §5.2 rule 3-4 (citekey format, citation
verification gate)

## Intent and Why

Fase 3 gives Research MCP a *single source of truth* for citations: every
paper already saved to a project gets a deterministic, stable BibTeX/CSL-JSON
bibliography, and every `[@citekey]` written in a draft can be checked against
that source of truth before it ever reaches a published document. This is the
project's explicit "Zero-Hallucination Gate" (AGENTS.md §1.2): no citation may
reach a final document without first passing `verify_citations` against the
project's own library.

## Goals

- `generate_bibliography(project_id, formats)`: (re)build
  `references.bib` and/or `references.csl.json` at the project root from the
  current `papers` table, deterministically, every call.
- `verify_citations(project_id, markdown_text=None)`: parse Pandoc-style
  citation syntax (`[@key]`, `[@key1; @key2]`, bare `@key`), and report (a)
  citekeys referenced that do not exist in the project's library
  ("hallucinated"/typo citations) and (b) papers that exist in the library but
  are never cited in the scanned text.
- Correct, per-paper BibTeX entry type (`@article`, `@inproceedings`, …) and
  CSL `type` (`article-journal`, `paper-conference`, …), derived from each
  provider's own publication-type data already present in responses we
  already fetch — no new HTTP calls.
- `citations/bibtex.py` gains `.bib`/CSL-JSON entry builders (extends, not
  replaces, Fase 2's `generate_citekey`).
- `citations/verifier.py` is new: pure parsing/diff logic, no I/O, easy to
  unit test exhaustively.

## Non-Goals

- Rendering formatted in-text citations or a formatted reference list in a
  specific style (APA/IEEE/Vancouver) — that is Fase 4/5's Pandoc
  Citeproc/WordCraft integration, which consumes the `.bib`/`.csl.json` this
  phase produces.
- Reading draft content from the database — `draft_sections` only stores
  metadata (filename, order, title, word count) until Fase 4's
  `save_draft_section` exists. This phase reads physical `.md` files from
  `workspace/<slug>/drafts/` directly (see "Draft scanning" below).
- Parsing the full Pandoc citation grammar (locators, suppress-author `-@key`
  prefixes/suffixes, custom `csl-entry` extensions). We extract every
  citekey-shaped token; an open question for whether we also want
  suppress-author depth is treated as "my call" below (we treat `[-@key]` the
  same as `[@key]` for verification purposes — a suppressed author is still a
  citation that must exist in the library).
- Changing `search_papers`' truncated-abstract response shape, or any
  Fase 1/2 tool signature. `item_type` is purely additive.

## Anti-Goals

- A citekey/entry scheme where the `.bib` file's entry ID can ever diverge
  from the citekey used in `[@citekey]` syntax. (Caught during brainstorming:
  pasting Crossref's raw `bibtex_raw` verbatim would use *Crossref's* internal
  key, not ours, silently breaking citation resolution. Every `.bib` entry's
  **ID** is always our own `citekey` — whether the entry's fields come from
  re-keyed `bibtex_raw` (§0.A) or from our structured fields.)
- Silently fabricating bibliographic data (author, year, venue) that a paper
  doesn't have. Missing fields are simply omitted from the entry (BibTeX) or
  left out of the CSL object — never guessed.

## Constraints

- Same as Fase 1/2: strict typing/Pydantic v2, `httpx`+`tenacity` for any new
  network code (none needed here beyond the field additions), path-traversal
  protection for file writes, no `print()` to stdout, `ruff check`/`ruff
  format --check`/`pytest` must stay green.
- `references.bib`/`references.csl.json` live directly under
  `workspace/<slug>/` (AGENTS.md §2.2 directory tree), not in a subfolder.
- Schema changes follow the precedent set in Fase 2's `external_id` addition:
  additive column, `_migrate_legacy_schema` handles pre-existing databases,
  `CREATE TABLE IF NOT EXISTS` already covers fresh ones.

## Approaches Considered (for the one open decision)

**Decision: how to classify each paper's publication type for BibTeX/CSL.**

- **A — Uniform constant** (`@article`/`"article-journal"` for every paper,
  regardless of source). Simplest, zero schema/provider changes. Rejected by
  the user: preprints and conference papers would be mislabeled.
- **B — Normalize real per-provider type data into a new `item_type` field**
  (chosen). Each of OpenAlex, Crossref, Semantic Scholar, and PubMed already
  returns publication-type information in the same response we already parse
  (OpenAlex/Crossref: a `type` string field; Semantic Scholar: add
  `publicationTypes` to the existing `fields` request string, no new round
  trip; PubMed: an additional XML element in the already-fetched
  `efetch` response). arXiv has no such field because every arXiv result is
  structurally a preprint, so it's hardcoded. This is the approach below.
- **C — Call a type-detection heuristic on title/venue text.** Rejected:
  unreliable, and the providers already tell us directly; inventing a
  heuristic over real data would be worse engineering, not better.

## The How (my calls, documented)

### 0. Sources of truth for `.bib` and `.csl.json`

Two decisions landed here (user-confirmed):

**A — `.bib` entries reuse the paper's raw Crossref BibTeX, re-keyed to our
citekey.** `papers.bibtex_raw` (populated by Crossref's `get_bibtex` since
Fase 2) is the authoritative, publisher-supplied source for a paper's
bibliographic detail: pages, volume, publisher, and a correctly-split
`author = {Family, Given and ...}` list that we would otherwise lose by
rebuilding from flat strings. `generate_bibliography` therefore:
1. Parses `bibtex_raw` with `bibtexparser` to get the one entry it contains;
2. Rewrites the entry's **ID** to our citekey (so `[@citekey]` resolves), and
   its `doi`/`title` fields to our `PaperModel` values if the raw entry is
   missing them;
3. Leaves every other raw field (author, pages, volume, publisher, journal,
   year, …) exactly as Crossref supplied it.
For papers with no `bibtex_raw` (arXiv, Semantic Scholar, OpenAlex, PubMed,
local PDFs), the entry is built from our structured fields — the fallback
described in §2/§3/§4.

**B — Author names are stored structured where the provider supplies them.**
Crossref and PubMed already return `given`/`family` (Crossref `author.given`/
`author.family`) and `ForeName`/`LastName` (PubMed) separately, but the
Fase 1/2 mappers flatten these to a single `"given family"` string and the
structure is lost before `SearchResult` is built. To stop guessing multi-word
family names ("van der Berg"), we now carry that structure through end to end
(see §3). The flat `authors: list[str]` stays for citekey generation, display,
and back-compat; structured names are additive.

### 1. Normalized `item_type` vocabulary

A small closed vocabulary, one value per paper, stored verbatim (not a CSL or
BibTeX string — mapped to each at bibliography-generation time so the mapping
can be tuned later without a schema change):

```
"journal-article" | "proceedings-article" | "book-chapter" | "preprint"
| "dataset" | "report" | "review" | "other"
```

Per-provider raw-value mapping (`providers/base.py`, new
`normalize_item_type(source_provider, raw_type) -> str`, single shared
function so every provider mapper calls the same table instead of five
copies):

| Provider | Raw field | Mapping |
|---|---|---|
| OpenAlex | `work["type"]` | `article`→journal-article, `preprint`→preprint, `book-chapter`→book-chapter, `dataset`→dataset, `report`→report, `review`→review, else→other |
| Crossref | `item["type"]` | `journal-article`→journal-article, `proceedings-article`→proceedings-article, `book-chapter`→book-chapter, `dataset`→dataset, `report`/`monograph`→report, `preprint`/`posted-content`→preprint, else→other |
| Semantic Scholar | first recognized entry in `publicationTypes` list | `JournalArticle`→journal-article, `Conference`→proceedings-article, `Review`→review, `Dataset`→dataset, else→other; empty/missing list→other |
| PubMed | first recognized `PublicationType` text | `Journal Article`→journal-article, `Review`→review, else→other (PubMed records are virtually always journal articles, so `other` is a safe, non-misleading fallback) |
| arXiv | — (hardcoded) | always `preprint` |
| Local PDF, no Crossref match | — | `other` |
| Local PDF, Crossref match | inherits Crossref's mapped value | — |

### 2. BibTeX entry type / CSL `type` mapping

One table in `citations/bibtex.py`, consumed only at bibliography-generation
time:

| `item_type` | BibTeX entry | CSL `type` |
|---|---|---|
| journal-article | `@article` | `article-journal` |
| proceedings-article | `@inproceedings` | `paper-conference` |
| book-chapter | `@incollection` | `chapter` |
| preprint | `@misc` | `article` |
| dataset | `@misc` | `dataset` |
| report | `@techreport` | `report` |
| review | `@article` | `review` |
| other | `@misc` | `document` |

(`preprint`→`@misc` matches real-world convention: arXiv's own "export
BibTeX" button emits `@misc` with `eprint`/`archivePrefix` fields, not a
nonstandard `@preprint`. CSL has no dedicated preprint type as of 1.0.2;
`"article"` is the closest CSL type without implying a specific journal.)

### 3. Author name splitting (BibTeX `Family, Given` / CSL `{family, given}`)

**Structured path (primary).** A new `SearchResult.authors_structured` field
(`list[AuthorName] | None`, where `AuthorName` is
`{given: str|None, family: str|None, literal: str|None}`) is populated by
providers that expose separate given/family parts:

- **Crossref** (`crossref.py:_map_item`): for each author object, `given` →
  `given`, `family` → `family` (the same values already joined for the flat
  `authors` list). Both are present for normal personal authors; a
  `family`-only corporate/organization author becomes `literal`.
- **PubMed** (`pubmed.py:_to_result`): `ForeName` → `given`, `LastName` →
  `family`; `CollectiveName` → `literal`.
- **OpenAlex / Semantic Scholar / arXiv**: do not expose structured given/
  family in the data we fetch, so `authors_structured` is `None` for these,
  and the heuristic fallback below applies.

This structured list is stored in a new `papers.authors_structured_json`
column (additive, alongside the existing `authors_json`) and read back into
`PaperModel.authors_structured` so it survives round-trips through
`save_paper_and_sections`, exactly like the existing `authors_json` field.

**Heuristic fallback (secondary).** When `authors_structured` is `None`, the
same flat string is split: last whitespace token is family, everything before
is given. A single-token name (mononym, or a PubMed `CollectiveName` already
handled as `literal`) becomes CSL `"literal"` and BibTeX written as-is.

The `authors_structured` data is the authoritative source for both CSL-JSON
`author` objects and the `.bib` fallback builder. The re-keyed Crossref
`.bib` path (paper has `bibtex_raw`) keeps Crossref's own `author` field
verbatim instead (correct split by construction), per §0.A.

### 4. BibTeX field escaping

BibTeX field values are brace-delimited; unbalanced literal `{`/`}` in a
title/venue would corrupt the file. Policy: before writing any field value,
strip literal `{`/`}` characters outright (lossy but never produces a
malformed `.bib`). Backslashes are left untouched (legitimate in LaTeX-styled
titles; a stray backslash doesn't break brace-matching, only possibly
mis-typesets — out of scope here). Every field value is wrapped in a single
outer `{...}` group (the portable, maximally-compatible form — avoids the
`"..."`-vs-`{...}` style question entirely).

### 5. Citation key character class for the verifier

Our own `generate_citekey` only ever emits `[a-z][a-z0-9]*` (plus letter
suffixes). The verifier matches the slightly more permissive
`[A-Za-z][A-Za-z0-9_:.#$%&\-+?<>~/]*` (Pandoc's own citeproc key-character
set, minus characters that would never appear from our generator but keeping
the regex honest to Pandoc's actual grammar in case a user hand-edits a
draft). A bare `@key` token must be preceded by start-of-string, whitespace,
or one of `[-;` so `foo@bar.com` and `@handle` mid-sentence are never
mistaken for a citation; immediately-preceding `-` is consumed as an optional
"suppress author" marker (`[-@key]`) and treated identically to `[@key]` for
verification (still a real citation that must resolve).

### 6. Draft scanning when `markdown_text` is omitted

`draft_sections` has no content column until Fase 4. When
`verify_citations(project_id, markdown_text=None)` is called, scan every
`*.md` file directly under `workspace/<slug>/drafts/` (already created per
project since Fase 1's `create_project`), concatenate their text, and run the
same citekey extraction over the concatenation. If the directory has no `.md`
files yet, this degenerates to "zero citations found, N papers uncited" —
correct and non-surprising, not an error.

### 6b. Builder-check resolutions (post-review, my calls)

A builder-check pass verified the four "already fetched" provider claims and
the `item_type` table's internal consistency (both confirmed correct with no
changes needed), and raised three questions. All three are implementer-level
correctness decisions, not product tradeoffs, so I resolved them directly
rather than opening another round:

1. **`papers.csl_json` (dormant since Fase 1/2).** `generate_bibliography`
   now also writes each paper's own single CSL-JSON object back into its
   `papers.csl_json` row (one object, not the aggregate array), mirroring
   how Crossref's raw BibTeX already populates `bibtex_raw` during
   ingestion. This keeps the column's evident purpose (per-paper CSL data)
   actually populated, and is purely additive — no existing reader depends
   on it being empty.
2. **Multi-valued type lists (PubMed `PublicationTypeList`, Semantic
   Scholar `publicationTypes`).** "First entry in API order" would
   misclassify review articles as plain journal articles, since NLM/S2
   convention lists `"Journal Article"` before `"Review"` for review pieces.
   Changed to a priority scan, not list order:
   `review` > `proceedings-article`/`Conference` > `journal-article` > first
   other recognized value > `other`. Applies independently to both
   providers' lists.
3. **Bare `@key` trailing punctuation.** The citekey character class would
   otherwise swallow a sentence-ending period/comma/etc. (`@smith2020.` at
   the end of a sentence), producing a false "missing citation". Fixed by
   stripping a trailing run of `.,;:!?` from a *bare*-form match only (the
   bracketed `[@key]` form is already bounded by `]` and unaffected). This
   matches Pandoc's real citeproc behavior, which the design already claims
   to follow.

### 7. `generate_bibliography` output contract

- Always rebuilds both files from the current `papers` table snapshot (full
  overwrite, not incremental) — matches the spec's "membangun ulang" wording
  and avoids any possibility of a stale leftover entry.
- `formats: list[str] = ["bibtex", "csl_json"]` controls which file(s) get
  written this call; the other is left untouched on disk. Unrecognized
  values in `formats` raise a `ToolError` (actionable, per AGENTS.md §5.2
  rule 2) rather than being silently ignored.
- Per-paper source of truth for each entry: `bibtex_raw` (re-keyed) when
  present, otherwise structured fields (see §0.A). A paper whose `bibtex_raw`
  exists is emitted with Crossref's own fields (author/pages/volume/
  publisher/journal/year) and only its ID + missing `doi`/`title` normalized
  to ours.
- When `"csl_json"` is requested, each paper's own `papers.csl_json` column
  is also updated with its individual CSL object (see §6b.1), in the same
  pass that builds the aggregate `references.csl.json` file. CSL objects are
  always built from structured fields (there is no "raw CSL" source); their
  `author` list uses `authors_structured` when present, else the heuristic
  split.
- Returns `{"bib_path": str | None, "csl_json_path": str | None, "paper_count": int}`.

### 8. `verify_citations` output contract

```python
{
    "cited_but_missing": list[str],   # citekeys referenced in text, not in the project's library
    "uncited_papers": list[dict],     # {"citekey": ..., "title": ...} for library papers never cited
    "cited_and_found": list[str],     # citekeys referenced in text that do resolve (for a quick sanity count)
    "source": "markdown_text" | "drafts_directory",
}
```

Called with `strict_citations=True` from the Fase 4 exporters (future phase):
a non-empty `cited_but_missing` must halt export, per AGENTS.md §5.2 rule 4.
This phase only builds the checking tool itself; wiring the gate into
exporters is Fase 4 scope.

## Schema Change

`papers` gains two columns, both following the exact precedent of Fase 2's
`external_id`:

```sql
ALTER TABLE papers ADD COLUMN item_type TEXT;
ALTER TABLE papers ADD COLUMN authors_structured_json TEXT;
```

Both added to `SCHEMA_SQL`'s `CREATE TABLE` for fresh databases, and to
`_migrate_legacy_schema` for pre-existing ones (same guarded
`PRAGMA table_info` pattern already in `db/database.py`). No new index
needed — neither column is an identity key.

## What's Left to the Builder

- Exact regex construction and test cases for the citekey extraction.
- Whether `normalize_item_type` lives in `providers/base.py` (shared) or is
  duplicated per-provider — design says shared; builder wires the five call
  sites.
- Test fixtures for each provider's new raw-type field.
- Exact `.bib`/CSL-JSON field set per entry (title, author, year, venue/
  container-title/journal, doi, url) — derived straightforwardly from
  `PaperModel`'s existing fields; no new fields needed beyond `item_type`.
