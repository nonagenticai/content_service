"""Re-export ORM models from the canonical database.models module.

content_service uses a single Base declared in src.database.models that
covers all five tables (storyline_jobs, narration_jobs, script_jobs,
script_batches, script_usage).  This shim keeps imports inside the
repository layer working without duplicating Base or table definitions.
"""

from src.database.models import Base, ScriptBatch, ScriptJob, ScriptUsage

__all__ = ["Base", "ScriptBatch", "ScriptJob", "ScriptUsage"]
