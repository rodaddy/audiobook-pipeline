"""External API clients for metadata resolution.

Submodules:
    audible -- Audible catalog search client with expanded response_groups.
              Returns subtitle, publisher_summary, publisher_name, copyright,
              language, and genre (from category_ladders) in addition to core
              fields. Logs query params, result counts, and API warnings.
    audnexus -- Chapter timings by ASIN from https://audnex.us, for books whose
              audio carries no marks of its own (loose MP3 where the file
              boundaries are encoding splits, not chapters). Gated hard before
              any mark is used: isAccurate must not be false, local duration
              must match the described edition within 1% AND 600s, and the
              table must be monotonic and in range. Offsets are used RAW --
              brandIntro/brandOutro are logged, never subtracted. A rejection
              returns [] and the caller keeps file-boundary chapters, because
              a plausible wrong chapter map is worse than a coarse right one.
    search  -- Fuzzy scoring and path hint extraction. Logs scoring details,
              best match with score, and path parsing results. Title scoring
              (_title_score) compares against both the full candidate title
              and its pre-subtitle head, taking the better, so an Audible
              subtitle the source folder omits ("Forsworn: A Powder Mage
              Novella") cannot sink a correct match below a different
              author's exact-title book. Weights: title 60%, author 30%,
              result-order bonus 10% -- callers MUST pass the author hint,
              or that 30% is silently zero.
"""
