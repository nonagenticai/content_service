"""Narrative route sub-package.

Split out of the former 1118-line ``src/api/narrative_routes.py`` (storyline /
shotlist / narration / extract handlers + shared helpers). ``narrative_routes.py``
is kept as a thin facade that aggregates the routers and re-exports the public
symbols so existing import paths keep working.
"""
