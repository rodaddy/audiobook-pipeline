# Library Reorganize Audit

## Summary

Systematic reorganize of all author folders in `/Volumes/media_files/AudioBooks/`.
Total: 82 author folders, ~600 books processed.

## Issues Found & Fixed

| Issue | Fix |
|-------|-----|
| Series name strips title when flowing (e.g. "Elminster in Hell" -> "in Hell") | Changed regex to require separator char or "Book N" before stripping |
| Michael Livingston had full WoT series inside "Origins" folder | Moved Origins to Robert Jordan, deleted duplicate WoT Books 0-11 |
| Robert Jordan had old unnumbered folders (12, 13, 14) alongside "Book N -" | Removed duplicates, fixed "A Fire Within the Ways" (not Book 14) |
| Steven Erikson split: "Malazan Book of the Fallen" vs "...Series" | Merged into single folder |
| Joe Abercrombie split three ways: "Trilogy", "World", "The First Law" | Merged all into "The First Law", renumbered by reading order (1-7) |
| Margaret Weis Deathgate Cycle split across 3 folders/authors | Consolidated into "Margaret Weis, Tracy Hickman/Deathgate Cycle" |
| Raymond E. Feist split: "Riftwar Saga" vs "Riftwar Cycle" + nesting | Merged, unnested Silverthorn |
| Dennis E. Taylor "Dennis E.m4b" (bad ASIN title for Heaven's River) | Renamed to Book 4 - Heaven's River, moved into Bobiverse |
| John Gwynne "Shadow of the Gods" truncated to "Shadow of the" | Manually fixed, reorganized Bloodsworn Trilogy folder |
| George R.R. Martin "Book 5 - A Feast for Crows" contained Dance with Dragons | Renamed correctly, moved Fire & Blood to subfolder |
| F. Scott Fitzgerald folder name with apostrophe + brackets | Renamed folder, fixed truncated ASIN title |
| Ken Liu Book 3 ASIN resolved to _unsorted (author missing) | ASIN lookup failure -- leave for manual fix |

## Code Fix Applied This Session

**`_build_library_filename` series stripping** (`stages/organize.py`):
- Old: stripped series name with optional separator
- New: requires explicit separator (`:`, `-`, `,`) or "Book N -" after series name
- Prevents "Elminster in Hell" -> "in Hell" while still fixing "WoT Book 11 - Knife of Dreams"

## Per-Folder Results

| Folder | Books | Status |
|--------|-------|--------|
| Robert Jordan | 22 | Done - WoT + Origins + World of + A Fire Within the Ways |
| Dragonlance | 47 | Done - .author-override marker, 45/47 (2 ffmpeg errors) |
| Sarah J. Maas | 14 | Done - ACOTAR + Throne of Glass |
| Robin Hobb | 33 | Done - 6 series + novellas |
| R. A. Salvatore | 58 | Done - merged 3 author variants |
| Lois McMaster Bujold | 44 | Done - Vorkosigan + Sharing Knife + Chalion |
| Craig Alanson | 34 | Done - Expeditionary Force + Mavericks |
| J. R. R. Tolkien | 16 | Done - LOTR + Hobbit + Silmarillion + standalones |
| Naomi Novik | 17 | Done - Temeraire + Scholomance |
| Ed Greenwood | 14 | Done - Elminster + Sage of Shadowdale (re-run after fix) |
| Michael J. Sullivan | 16 | Done - Riyria Revelations/Chronicles + Legends + Rise and Fall |
| James S. A. Corey | 15 | Done - The Expanse (all 9 + novellas) |
| Brandon Sanderson | 14 | Done - Mistborn Saga + Wax and Wayne |
| Anne Rice | 12 | Done - Vampire Chronicles |
| John Gwynne | 8 | Done - Faithful/Fallen + Blood/Bone + Bloodsworn (manual fix) |
| Steven Erikson | 10 | Done - Malazan (merged split folders) |
| Joe Abercrombie | 10 | Done - The First Law (merged 3-way split, renumbered) |
| Richard Morgan | 8 | Done - Takeshi Kovacs + standalones |
| Andrzej Sapkowski | 8 | Done - The Witcher |
| Margaret Weis, Tracy Hickman | 7 | Done - Deathgate Cycle (consolidated) |
| Margaret Weis | 7 | Removed - duplicates merged into joint author |
| Michael Livingston | 13 | Removed - WoT duplicates, Origins moved to R. Jordan |
| Jacqueline Carey | 6 | Done - Phedre + Imriel trilogies |
| B. T. Narro | 5 | Done - Rhythm of Rivalry |
| Pierce Brown | 6 | Done - Red Rising |
| Lev Grossman | 4 | Done - The Magicians |
| Mark Lawrence | 3 | Done - Broken Empire |
| R. Scott Bakker | 3 | Done - Prince of Nothing |
| Gene Wolfe | 5 | Done - Book of the New Sun |
| Douglas Adams | 5 | Done - Hitchhiker's Guide |
| Dennis E. Taylor | 5 | Done - Bobiverse (manual fix for Heaven's River) |
| Andrew Rowe | 5 | Done - Arcane Ascension |
| Tad Williams | 4 | Done - Bobby Dollar |
| Ryan Cahill | 3 | Done 2/3 - Bound and the Broken (1 ffmpeg error) |
| Patrick Rothfuss | 3 | Done - Kingkiller Chronicles |
| Brent Weeks | 3 | Done - Night Angel |
| Cixin Liu | 3 | Done - Remembrance of Earth's Past |
| James Islington | 3 | Done - Licanius Trilogy |
| Fonda Lee | 3 | Done - Green Bone Saga |
| Scott Lynch | 3 | Done - Gentleman Bastard |
| Suzanne Collins | 3 | Done - Hunger Games |
| Neil Gaiman | 3 | Done - American Gods series |
| Raymond E. Feist | 3 | Done - Riftwar Saga (merged split) |
| George R.R. Martin | 7 | Done - ASOIAF + Fire & Blood (manual fixes) |
| Ken Liu | 4 | Partial - Dandelion Dynasty (Book 3 ASIN failure) |
| Matt Dinniman | 1 | Done - Dungeon Crawler Carl |
| Jenn Lyons | 1 | Done - Chorus of Dragons |
| F. Scott Fitzgerald | 1 | Done - renamed folder + fixed title |
| All remaining singles | ~30 | Done - standalones organized |

## Remaining Issues

- Ken Liu Book 3: ASIN author resolution failure (went to _unsorted)
- Ryan Cahill Book 3: ffmpeg tagging error (cover art codec issue)
- Dragonlance 2 books: ffmpeg tagging errors from previous run
- `_unsorted/`, `_incoming/` folders not processed (intentional)
