"""External API clients for metadata resolution.

Submodules:
    audible -- Audible catalog search client with expanded response_groups.
              Returns subtitle, publisher_summary, publisher_name, copyright,
              language, and genre (from category_ladders) in addition to core
              fields. Logs query params, result counts, and API warnings.
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
