# api

External API clients for metadata resolution.

Submodules:
    audible -- Audible catalog search client with expanded response_groups.
              Returns subtitle, publisher_summary, publisher_name, copyright,
              language, and genre (from category_ladders) in addition to core
              fields. Logs query params, result counts, and API warnings.
    search  -- Fuzzy scoring and path hint extraction. Logs scoring details,
              best match with score, and path parsing results.

---
*Auto-generated from `__init__.py` docstring by `scripts/gen-readme.py`.*
