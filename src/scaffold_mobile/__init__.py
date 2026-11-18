"""Mobile scaffold directory for WebdriverIO-based app demo projects.

Provides the TypeScript project template (package.json, tsconfig.json,
wdio config, and mobile-demo-helpers library) that gets copied into each
mobile job's working directory before script generation.
"""

from pathlib import Path


def get_mobile_scaffold_dir() -> Path:
    """Return the absolute path to the mobile scaffold directory."""
    return Path(__file__).parent
