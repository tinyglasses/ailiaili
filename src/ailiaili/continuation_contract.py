from __future__ import annotations

import hashlib
from pathlib import Path


# These files determine how inherited world state becomes new creators,
# recommendations, viewer actions, persistence rows and billed model calls.
# Reporting, diagnostics, CLI wording and the snapshot utility itself are
# intentionally excluded: changing an observation layer must not invalidate a
# runnable branch.
_CONTRACT_PATHS = (
    "config.py",
    "continuation_contract.py",
    "domain.py",
    "persona.py",
    "recommendation.py",
    "stubs.py",
    "utils.py",
    "creator/observation.py",
    "creator/prompt.py",
    "creator/service.py",
    "llm/config.py",
    "llm/gateway.py",
    "llm/provider.py",
    "persistence/database.py",
    "persistence/models.py",
    "simulation/runner.py",
    "viewer/behavior.py",
    "viewer/prompt.py",
    "viewer/service.py",
)


def continuation_code_sha256() -> str:
    """Fingerprint the executable contract that can change future days."""

    package_root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for relative in _CONTRACT_PATHS:
        path = package_root / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
