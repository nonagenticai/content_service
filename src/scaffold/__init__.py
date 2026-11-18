"""Scaffold directory for Playwright demo script projects.

Provides the TypeScript project template (package.json, tsconfig.json,
playwright config, and demo-helpers library) that gets copied into each
job's working directory before script generation.
"""

from pathlib import Path


def get_scaffold_dir() -> Path:
    """Return the absolute path to the scaffold directory."""
    return Path(__file__).parent
