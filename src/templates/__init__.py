"""Template loading infrastructure for script generation prompt templates."""

from pathlib import Path


def get_templates_dir() -> Path:
    """Return the path to the templates directory.

    Uses the location of this file to resolve the templates directory,
    ensuring it works regardless of the current working directory or
    how the package is installed.
    """
    return Path(__file__).parent
