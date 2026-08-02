# The v1.0.0 rewrite, in order

**Status:** PLAN. Nothing below is built. Every step is PROPOSED until its own
verification line is observed.

**Measured 2026-08-02** against `fix/library-diff-source-formats` @ `bedd8f5`
(14 commits ahead of `origin/main`): 32 source files / 9,134 lines, 35 test
files / **590 tests passing in 3.22s**, 3,781 lines of superseded bash still
tracked at the repo root.

**Owns:** the v1.0.0 community release. **Blocks:** the acceptance conversion
run against `/Volumes/ThunderBolt/CleanDesktop/tFiles/Done/`.

The controlling standards are `_DOCS/STANDARDS-python.md` and
`_DOCS/CODING_STANDARDS.md`. The worked example is
`_DOCS/python-exemplar/`. This file is the **sequence**: what lands in what
order, what proves each step, and what must not come back.

---

## EXECUTION CONTRACT — read first, every session

Adapted from `open-brain/_plans/python-port-sequence.md`, whose rules were each
written after a prior session violated them. Rules 1-3 and 5 are inherited
verbatim in spirit; 4 and 6 are specific to this repo.

1. **Never read the old source to answer a question.** `_DOCS/legacy-source/`
   exists to be re-derived FROM, not copied. When a fact lives only in the old
   code, write a **stub**, record the question in `## Open questions` below, and
   ask. Do not import the answer. This rule is the entire reason the rewrite
   finds bugs an in-place refactor cannot: copying forward preserves the defect
   along with the behaviour.

   The exception, and it is narrow: the **tests** are not the old source. They
   are the specification, they come forward intact, and they are the acceptance
   harness for every step.

2. **Web-search before building any mechanism.** Existing repo helper →
   well-known **maintained** library → stdlib → custom, in that order. Hand-
   rolling a solved problem is a defect. The decisions already taken are in
   `## Library decisions`; a decision recorded there is settled — reopen it only
   with a new fact, and write the new fact down.

3. **End-to-end before hardening.** Open Brain measured its own failure here:
   *7 commits, 8 modules, 184 tests — and zero database writes.* Components
   hardened in isolation while the application did not exist. **Step 6 (the
   spine) outranks every polish, refactor, or coverage task** until one real
   book converts end to end.

4. **The 590 tests are the contract.** They stay green at every step, or the
   step is not done. Eleven of them encode bugs that took a full session of
   digging to find (see `## The eleven defects that must not return`). A test
   that fails because the rewrite changed a shape gets its *shape* updated; a
   test that fails because behaviour regressed means stop.

5. **Testing budget.** Goal is all pieces DONE, not each piece tested to death.
   After-the-fact testing stays well under 30% of coding time per piece. Cheap
   gates every step (`pytest`, `ruff`, `mypy`, `check_code_size`); ONE hard
   testing pass when every piece exists.

6. **A decision made in conversation is written into a file the same session.**
   Open Brain's rule 7, quoted because it is load-bearing: *"you said parked it,
   but you didn't write a file... it's gonna be fucking forgotten."* Plans and
   decision records are the durable copy. The chat is not.

---

## Why a rewrite and not a refactor

The in-place case was argued and lost on measured evidence, not preference:

| Finding | Measurement |
|---|---|
| **Zero Pydantic models in the application** | `rg -c 'BaseModel' src/` → no matches. `models.py` is enums + one `@dataclass`. |
| Untyped dicts across every boundary | 68 `dict[str, Any]` / `-> dict` sites in 15 files; `pipeline_db.py` alone has 10 |
| Hand-rolled `.env` parser | `cli.py:28-50`, 22 lines, silently skips `${VAR}` lines — while `pydantic-settings` is already a declared dependency |
| Keystone rule broken | `cli_audit.py:122` reads `os.environ["PLEX_TOKEN"]` directly |
| Files over the 500-line ceiling | `ops/organize.py` 815, `ops/audit.py` 792, `pipeline_db.py` 704, `convert_orchestrator.py` 524 |
| Raw SQL | 22 statements in `pipeline_db.py` |

Converting every dict boundary to a validated model, splitting four oversized
files, and replacing the config plumbing **is** a rewrite. Doing it as a chain
of in-place edits is the same work performed without a net, against a codebase
that has no type safety to catch what shifts underneath.

---

## What we are rewriting FROM

`src/audiobook_pipeline/` moves to `_DOCS/legacy-source/`. It is reference
material under contract rule 1 — read to re-derive a decision, never to copy an
answer.

**Note the repo has done this once already.** `bin/`, `lib/`, and `stages/` at
the root are 3,781 lines of the original bash implementation, still tracked,
superseded by the Python port. They are retired as part of step 0 — not deleted
by an agent (`AGENTS.md`: an agent never runs a recursive or forced delete), but
moved and proposed for Rico's hand.

---

## Library decisions

Contract rule 2, applied. Verified 2026-08-02. **A decision here is settled.**

| Mechanism | Decision | Verified reason |
|---|---|---|
| Config loading | **`pydantic-settings`** | Already a dependency. Replaces the 22-line hand-rolled `.env` parser. `settings_customise_sources` makes the documented precedence a property of the mechanism, not a docstring claim — this is the exact bug the exemplar's own `config.py` shipped and fixed. |
| MP4 tag writing | **`mutagen`** | Confirmed against mutagen's MP4 API docs: freeform atoms (`----:com.apple.iTunes:ASIN`) are supported for write. **Removes the external `mp4tags`/mp4v2 dependency**, which today is an optional install whose absence silently costs ASIN tags. |
| Retry / backoff | **`tenacity`** | Actively maintained. `stop_after_attempt` + `wait_exponential_jitter` + `retry_if_exception_type`. What survives as ours is the genuinely domain part: *which* exceptions deserve a retry. |
| HTTP | **`httpx`** | Already a dependency. Keep. |
| Fuzzy matching | **`rapidfuzz`** | Already a dependency. Keep. |
| Database | **SQLModel** *(provisional)* | Pydantic + SQLAlchemy; a contributor recognizes it. Retires 22 raw statements. **Provisional** — SQLAlchemy is the proven layer and SQLModel the newer skin; confirm at step 5 before writing the migration. |
| **ffmpeg / ffprobe** | **OWNED — documented exception** | `ffmpeg-python` is unmaintained. `ffmpy` (522 stars) only builds a command line and would remove zero lines of our parsing. `pyffmpeg` bundles its own binary — worse for us. Per `STANDARDS-python.md`, the module docstring names each rejected library and why. |
| **Chapter writing** | **OWNED — documented exception** | Verified against mutagen's own API reference: `MP4Chapters` is **read-only** (`moov.udta.chpl`). No maintained library writes chapters. FFMETADATA1 via ffmpeg is the real mechanism and is what we do. |

**On owning code:** the standard's exception is real but narrow. Where we own
it, the docstring names the library rejected and why, and states that its
implementation is what ours was checked against. Owning ffmpeg glue is not a
licence to skip reading the reference implementations.

---

## The eleven defects that must NOT return

Each was found this session, each has a regression test proven to fail against
the original code. A naive rewrite re-creates several of them, because each one
is the *obvious* implementation.

| # | Defect | Guard test |
|---|---|---|
| 1 | Diff scanned only `*.m4b`, hiding 76 source files | `test_library_diff.py` |
| 2 | Part-naming never collapsed → 118 phantom entries | `test_library_diff.py` |
| 3 | A loose file at a collection root pruned the entire tree (11 book dirs → 1) | `test_runner_discovery.py` |
| 4 | `m4b_count > 1` called 40 novels "chapters" — would concat 510 hours | `test_separate_books.py` |
| 5 | Author never passed to `score_results` — 30% of the weight permanently zero | `test_search_scoring.py` |
| 6 | Author lost through a series folder (Pattern C stopped one level short) | `test_organize.py` |
| 7 | `0.5` split at the decimal | `test_organize.py` |
| 8 | An Audible subtitle sank the correct match | `test_search_scoring.py` |
| 9 | `Part N of M` leaked into titles and tags | `test_ops/test_organize.py` |
| 10 | **Embedded chapters discarded** — The Martian 160 → 0, Salvatore 184 → 0 | `test_chapter_preservation.py` |
| 11 | All 11 path defaults absolute — a fresh clone was unusable off-root | `test_config.py::test_no_default_path_escapes_the_project` |

Plus three found by the Sol review lane, independently verified: sole-surname
matching filed `Michael Williams` under `Tad Williams`; ffmpeg **silently
dropped** `ASIN`/`sort_album`/`publisher` atoms while reporting success; AAC
stream-copy passthrough avoided a lossy round trip.

**Defect 11 is the one a rewrite is most likely to reload**, because absolute
paths look like configuration. The property test asserts over ALL `Path` fields
rather than a list of names, so a new setting cannot reintroduce it silently.

---

## Target layout

Matches `_DOCS/python-exemplar/` and `open-brain/python/openbrain/`.

```
src/audiobook_pipeline/
├── __init__.py        full module docstring -> generates README.md
├── config.py          THE keystone. Nested BaseModel sections. Exempt from 500.
├── models/            Pydantic ONLY, no logic. The 68 dicts become these.
│   ├── book.py            SourceBook, BookDirectory, LibraryEntry
│   ├── chapter.py         Chapter, ChapterSet  (start_ms/end_ms/title)
│   ├── metadata.py        AudibleResult, AudnexusChapters, BookMetadata
│   ├── media.py           ProbeResult, StreamInfo, AudioFormat
│   └── stage.py           StageResult, StageStatus, PipelineLevel  (enums today)
├── utils/             the shared floor. Bottom of the import graph.
│   ├── logging_config.py  three sinks. Only consumer of config.LogSettings.
│   ├── ffmpeg.py          OWNED subprocess glue. Rejections in the docstring.
│   ├── http.py            httpx + tenacity. No hand-rolled backoff.
│   └── paths.py           sanitize, path building
├── db/                SQLModel. Retires 22 raw statements.
│   ├── engine.py
│   └── models.py
├── services/          business logic, ONE concern per module
│   ├── discover.py        find book directories  (defects 3, 4)
│   ├── concat.py          chapter preservation   (defect 10)
│   ├── convert.py
│   ├── identify.py        ASIN search + scoring  (defects 5, 8)
│   ├── tag.py             mutagen writes
│   ├── organize.py        path derivation        (defects 6, 7, 9)
│   └── diff.py            library comparison     (defects 1, 2)
└── apps/              entry points: parse args, build config, run
    ├── convert/
    └── audit/
```

`utils/` is the shared floor, not a junk drawer. A module earns a place by being
needed in two or more services AND depending on none of them. A helper used by
exactly one service belongs in that service.

Import concrete names from the submodule (`from ...models.chapter import
Chapter`), never re-export through `__init__.py` — that makes every `models`
import pull in every submodule, which is how an import cycle gets built by
accident.

---

## The sequence

Each step lands as its own commit. **Verification is observed, not assumed** —
a step is DONE when its proof line has been run and its output seen.

### Step 0 — stage the ground
Move `src/audiobook_pipeline/` → `_DOCS/legacy-source/`. Propose retirement of
`bin/`, `lib/`, `stages/` (3,781 lines of superseded bash) to Rico by `mv`.
Tests stay exactly where they are.
**Proves:** `git status` shows the move; no test file touched.

### Step 1 — `config.py`, the keystone
Nested typed `BaseModel` sections replacing 44 flat fields. `secrets/config.json`
layers + `AUDIOBOOK_` env prefix + `extra="forbid"`.
`settings_customise_sources` declares precedence. `load_settings()` is the only
sanctioned constructor and configures logging.
**Proves:** `test_config.py` green **including** the absolute-path property test
(defect 11); an `EXTRA_KEY` in a config file is REJECTED, not ignored.

### Step 2 — `utils/logging_config.py`
Three sinks: console, rotating file, structured JSON. Called exactly once, by
`load_settings`.
**Proves:** `test_logging.py` green; all three sink files observed on disk.

### Step 3 — `models/`
Every dict boundary becomes a validated model. This is where re-derivation
surfaces bugs: today nothing checks that a shape is what the next function
expects.
**Proves:** `test_models.py` green; a malformed Audnexus payload raises at
construction naming the field, instead of `KeyError` three frames later.

### Step 4 — `utils/` floor
`ffmpeg.py` (owned, rejections documented), `http.py` (httpx + tenacity),
`paths.py`.
**Proves:** `test_ffprobe.py` + `test_sanitize.py` green; zero hand-rolled retry
loops (`rg 'for attempt' src/` → no matches).

### Step 5 — `db/`
Confirm SQLModel vs SQLAlchemy FIRST (see Library decisions), then port.
**Proves:** `test_pipeline_db.py` + `test_db_migration.py` green against an
existing `pipeline.db`.

### Step 6 — THE SPINE. End-to-end before hardening.
Minimum path for ONE real book: discover → concat → convert → identify → tag →
organize. Stub anything not on that path.
**Proves:** one real book from `tFiles/Done/` converts to a chaptered, tagged
M4B in the library — chapters intact, ASIN present via `mp4info`.
**This step outranks every polish task. Nothing after it starts until it passes.**

### Step 7 — remaining services
Fill in the stubs: `diff.py`, audit checks, archive, cleanup.
**Proves:** all 590 tests green.

### Step 8 — apps + CLI
`apps/convert/`, `apps/audit/`. Click entry points, thin.
**Proves:** `test_cli.py` green; `--help` on both commands.

### Step 9 — ONE hard testing pass
Contract rule 5's deferred testing. mypy `strict = true`, ruff clean,
`check_code_size.py` passing, `_githooks/` installed and *proven to reject*.
**Proves:** each gate observed rejecting its own violation — a hook that has
never been seen to fail is not enforcement.

### Step 10 — docs + release
README (with the ~200 → 703 books / 712 files / 130 authors / 376 GB provenance
note), CREDITS (Audnexus, Audible, ffmpeg, seanap's Plex-Audiobook-Guide,
dependencies), CHANGELOG, AI on/off guide, reconcile `VERSION` 0.1.0 vs
`pyproject` 1.1.0. Squash to v1.0.0.
**Proves:** clean clone → `setup.sh` → convert a book, on a machine that is not
this one.

### Step 11 — ACCEPTANCE
The real conversion run against `tFiles/Done/`. **This is the test that decides
whether v1.0.0 publishes.**

---

## Open questions

Contract rule 1: a fact that lives only in the old code goes here, not into a
guess.

- **Library baseline drifted mid-session.** Source `m4b` dropped 50 → 24 (26
  Salvatore files) and the library grew 703 → 712, outside this session. Rico:
  *"I wouldn't really worry about it."* Recorded because step 11's acceptance
  run needs a known starting state to prove anything — re-measure at step 11,
  do not reconcile now.
- **SQLModel vs SQLAlchemy** — settle before step 5 writes a migration.
- **`.author-override` marker semantics** — confirm the exact precedence rule
  during step 6's `organize.py` re-derivation rather than reading it out of the
  old implementation.

---

## What must not come back

- Absolute path defaults (defect 11). Property-tested.
- `os.environ` reads outside `config.py`. The keystone rule has exactly one
  legitimate reader.
- A hand-rolled `.env` parser, retry loop, or config merge.
- `dict[str, Any]` at a module boundary.
- Any file over 500 code lines except `config.py`.
- A silently optional external binary whose absence costs metadata without
  saying so (today: `mp4tags`).

---

## See also

- `_DOCS/STANDARDS-python.md` — the standard this implements
- `_DOCS/python-exemplar/` — the worked example; read `config.py` first
- `open-brain/_plans/python-port-sequence.md` — the port this sequence is modelled on
- `open-brain/python/openbrain/src/openbrain/` — the same layout at application scale
