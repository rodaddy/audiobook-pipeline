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
| Config loading | **JSON file + `pydantic-settings`** | See `## Why JSON plus pydantic-settings` below — the two are not alternatives. Already a dependency. Replaces the 22-line hand-rolled `.env` parser. |
| MP4 tag writing | **`mutagen`** | Confirmed against mutagen's MP4 API docs: freeform atoms (`----:com.apple.iTunes:ASIN`) are supported for write. **Removes the external `mp4tags`/mp4v2 dependency**, which today is an optional install whose absence silently costs ASIN tags. |
| Retry / backoff | **`tenacity`** | Actively maintained. `stop_after_attempt` + `wait_exponential_jitter` + `retry_if_exception_type`. What survives as ours is the genuinely domain part: *which* exceptions deserve a retry. |
| HTTP | **`httpx`** | Already a dependency. Keep. |
| Fuzzy matching | **`rapidfuzz`** | Already a dependency. Keep. |
| Database | **stdlib `sqlite3` + a Pydantic row factory** | No ORM, no third-party layer. See `## Why no ORM` below. |
| **ffmpeg / ffprobe** | **OWNED — documented exception** | `ffmpeg-python` is unmaintained. `ffmpy` (522 stars) only builds a command line and would remove zero lines of our parsing. `pyffmpeg` bundles its own binary — worse for us. Per `STANDARDS-python.md`, the module docstring names each rejected library and why. |
| **Chapter writing** | **OWNED — documented exception** | Verified against mutagen's own API reference: `MP4Chapters` is **read-only** (`moov.udta.chpl`). No maintained library writes chapters. FFMETADATA1 via ffmpeg is the real mechanism and is what we do. |

**On owning code:** the standard's exception is real but narrow. Where we own
it, the docstring names the library rejected and why, and states that its
implementation is what ours was checked against. Owning ffmpeg glue is not a
licence to skip reading the reference implementations.

### Why no ORM

**Decision: stdlib `sqlite3` plus a Pydantic row factory. No SQLAlchemy, no
SQLModel, no third-party database layer.**

The rewrite's actual problem with `pipeline_db.py` was never "it uses SQL." It
was that rows cross the boundary as unvalidated dicts — 10 of the 68
`dict[str, Any]` sites live in that one file. An ORM fixes that as a side effect
of a great deal of other machinery. A row factory fixes exactly that.

Each table gets a Pydantic model whose fields match its columns:

```python
row = cursor.fetchone()                  # sqlite3.Row
record = StageRecord.model_validate(dict(row))   # validated on the way out
cursor.execute(INSERT, record.model_dump())      # typed on the way in
```

The schema derives from the model's fields, so the table and the type cannot
drift. That is what an ORM was supposed to buy — typed rows, validation, one
declaration of shape — without the session, the identity map, lazy loading,
relationship cascades, or a migration graph.

**Why an ORM is the wrong size here.** This is a single-writer local SQLite file
tracking stage status per book, recording failures for retry, and caching
lookups. There are no relationships to traverse and no query builder needed.
SQLAlchemy's surface — detached instances, `flush` vs `commit` timing, N+1 from
lazy loads — is real cost paid for capability this application does not use.
SQLModel does not avoid that; it is a thinner skin over the same engine.

**This also corrects a rule-2 misapplication.** The first draft of this plan
reached for "a well-known maintained library" and landed on SQLModel. The honest
reading of the preference order is that the **stdlib** rung comes before custom
code, and `sqlite3` is stdlib. The thing actually wanted from SQLModel was
Pydantic — which is already a dependency for `models/`. Reaching past the stdlib
for a dependency that supplies something already present is not applying the
rule, it is pattern-matching on the word "library."

**Deliberately NOT a hand-rolled ORM.** The factory validates and serializes; it
does not grow a query builder, relationship handling, or lazy loading. The
moment it wants one of those, that is the signal the data model outgrew SQLite,
and the answer is a real database — not a homegrown SQLAlchemy.

**Open at step 5, not now:** what `pipeline_db.py`'s existing version handling
and `test_db_migration.py` actually cover, and whether row models live in `db/`
or `models/`. Leaning `db/` — a table row is not a domain model, and conflating
the two is how ORMs earn their reputation.

### Why JSON plus pydantic-settings

**These are not competing choices.** JSON stays the config FORMAT;
pydantic-settings is HOW it is loaded, layered, and validated. A `config.json`
is exactly what `JsonConfigSettingsSource` reads. Recorded here because the
question came up as a candidate for the wider standard, and the answer is
"both", not "one or the other".

What the library adds over reading the file yourself:

1. **Precedence becomes a property of the mechanism.** The source tuple returned
   by `settings_customise_sources` IS the order, highest first — init kwargs,
   then env vars, then the JSON layers. Nobody traces code to learn whether an
   env var beats a file.

2. **Layering without a hand-written merge.** `json_file=[config.json,
   config.{env}.json]` with `deep_merge=True` layers the per-environment file
   over the base, so a file setting only `logging.level` leaves its siblings
   intact. That is the recursive merge we do not have to own — and per the
   standard's own table, "whether env vars beat files, or files beat env vars"
   is precisely the decision a hand-rolled merge makes invisibly.

3. **`extra="forbid"` turns a typo into a startup error.** A misspelled key is
   otherwise dropped silently and the default used, which presents as "my
   setting does nothing" with no error to search for.

4. **Validation at load, naming the field.** `Field(default=30, ge=5, le=3600)`
   fails at startup saying `convert.interval_seconds`, not 200 lines later
   inside a call that received a string.

**The receipt.** The exemplar shipped this bug and documents it: `config.py`
originally read the JSON itself and passed the result as `Settings(**values)`.
Init kwargs are pydantic's HIGHEST-priority source, so the JSON files silently
outranked environment variables — the exact reverse of what its own module
docstring promised. `EXEMPLAR_LOGGING__LEVEL=CRITICAL` against a file saying
`DEBUG` produced `DEBUG`, with nothing logged to show the variable had been read
and discarded. A documented precedence order the code does not implement is
worse than no documentation, because it is trusted.

**`.env` keeps one job:** naming which environment to load and holding secrets
for local development. It does not become a second config surface — two
competing file conventions is one too many.

**Where the files live: `config/`, not `secrets/`.** The exemplar puts its
layers in `secrets/` because everything it configures happens to be sensitive.
That is a property of that example, not a rule, and inheriting it mislabels an
entire directory — a reader who sees `secrets/config.json` reasonably assumes it
cannot be committed, and then the non-sensitive defaults everyone needs go
unshared.

This repo splits them by what they actually are:

```
config/                    NON-secret, COMMITTED, the shared defaults
├── config.json                base layer, safe to read and share
├── config.plex.json           per-environment layers
└── config.audiobookshelf.json
secrets/                   gitignored EXCEPT *.example and README.md
├── config.example.json        shows the SHAPE of the secret keys, no values
└── README.md
```

Only the API key and any tokens land in `secrets/`. Everything else — paths,
bitrates, regions, thresholds, the six named profiles — is `config/`, committed,
and diffable. The precedence chain reads both, secrets last so a key can
override, and the split costs nothing because `json_file=[...]` already takes a
list.

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
├── db/                stdlib sqlite3 + Pydantic row factory. No ORM.
│   ├── connection.py      connect, pragmas, schema init
│   ├── rows.py            row models; fields match columns exactly
│   └── queries.py         the SQL, one named function per statement
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

Outside `src/`, at the repo root — **shell scripts and dev tooling never live
inside the package**:

```
scripts/                   NOT importable, NOT shipped in the wheel
├── dev/                   developer tooling
│   ├── check_code_size.py     the 500/50 ceiling enforcement
│   ├── gen-readme.py          docstring -> README generation
│   ├── gen-requirements.py
│   └── demo-hooks.sh          proves each hook rejects its own violation
├── setup/
│   └── setup.sh               the guided installer (moves from examples/)
└── ops/
    └── find_untagged.py       library maintenance one-offs
config/                    NON-secret config layers. COMMITTED.
├── config.json                shared defaults
└── config.{profile}.json      per-profile layers
secrets/                   gitignored EXCEPT *.example and README.md
├── config.example.json        shape of the secret keys only, no values
└── README.md
examples/                  user-facing samples ONLY, no executable tooling
└── config/                the six named profiles
data/                      runtime state, gitignored
logs/                      gitignored
```

**Today this is wrong in two ways** (measured 2026-08-02): `scripts/` holds
three loose files with no subdirectories, and `examples/scripts/setup.sh` puts
executable tooling in a directory meant for samples a user reads. Step 0 fixes
both. The rule is that `examples/` is read, `scripts/` is run.

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

Also lay out `scripts/` properly, since the rewrite should not inherit the
current mess: `scripts/{dev,setup,ops}/`, with `examples/scripts/setup.sh`
moving to `scripts/setup/setup.sh`. `examples/` keeps config profiles only.
**Proves:** `git status` shows the moves; no test file touched; `fd . scripts`
shows every file inside a subdirectory.

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
stdlib `sqlite3` + Pydantic row factory (see `## Why no ORM`). Read what the
existing version handling actually does BEFORE writing the schema path — that
is the one genuinely open question here.
**Proves:** `test_pipeline_db.py` + `test_db_migration.py` green against an
existing `pipeline.db`; zero `dict[str, Any]` crossing the db boundary.

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

### Step 11 — ACCEPTANCE, and the library fix
The real conversion run against `tFiles/Done/`, using the finished v1.0.0 code.

**This step is not only a test — it is the repair.** The library drift noted
below gets reconciled here, by the tool, which is the entire reason the tool
exists. Re-measure the baseline first (source count, library count, diff
report) so the run has a known starting point, then convert.

**Proves:** the diff report goes to zero missing books, every converted file
carries chapters and an ASIN (`mp4info`), and Plex sees them. **This is the
test that decides whether v1.0.0 publishes.**

---

## Open questions

Contract rule 1: a fact that lives only in the old code goes here, not into a
guess.

1. **Existing schema/migration behaviour** — settle before step 5 writes the
   schema path. What does `pipeline_db.py`'s version handling actually do, and
   what does `test_db_migration.py` cover? A model-derived schema makes adding a
   column trivial; whether it covers what is already there is unverified.
   *(The ORM question is closed — see `## Why no ORM`.)*
2. **`.author-override` marker semantics** — confirm the exact precedence rule
   during step 6's `organize.py` re-derivation rather than reading it out of the
   old implementation. Specifically: does the marker beat an Audible-supplied
   author, or only a derived one?
3. **`PIPELINE_LEVEL` × stage filtering** — the four levels (simple / normal /
   ai / full) gate which stages run. Re-derive the matrix from the tests and the
   docs at step 6; do not copy the branch conditions forward.

**Not an open question — the library drift.** Source `m4b` dropped 50 → 24 (26
Salvatore files) and the library grew 703 → 712 outside this session. This is
not a mystery to be solved before starting and it is not a reason to hold the
plan. **Fixing it is what step 11 is for**: the acceptance run is the thing that
reconciles the library, using the finished v1.0.0 code. Re-measure the baseline
at step 11 so the run has a known starting point, then let the tool do its job.
That is the whole point of the acceptance test — the drift is the work, not a
blocker to it.

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
- An ORM. Also: a hand-rolled one. The row factory validates and serializes;
  if it starts wanting a query builder or relationship handling, the data model
  has outgrown SQLite and the answer is a real database, not homegrown
  SQLAlchemy.

---

## See also

- `_DOCS/STANDARDS-python.md` — the standard this implements
- `_DOCS/python-exemplar/` — the worked example; read `config.py` first
- `open-brain/_plans/python-port-sequence.md` — the port this sequence is modelled on
- `open-brain/python/openbrain/src/openbrain/` — the same layout at application scale
