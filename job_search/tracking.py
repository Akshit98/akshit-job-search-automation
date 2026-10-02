from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ALLOWED_STATES = {
    "new", "reviewed", "saved", "applied", "interview", "offer",
    "rejected", "closed", "not_pursuing",
}
PRIVATE_STATE_ENV = "JOB_SEARCH_PRIVATE_STATE"
FORBIDDEN_REPOSITORY_DIRS = ("output", "data", "config", ".github")


def resolve_private_state_path(root: Path) -> Path:
    configured = os.getenv(PRIVATE_STATE_ENV, "").strip()
    path = Path(configured).expanduser() if configured else root / "private" / "application_state.json"
    return path.resolve()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_private_state_path(path: Path, root: Path) -> Path:
    resolved = path.expanduser().resolve()
    repository = root.resolve()
    for directory in FORBIDDEN_REPOSITORY_DIRS:
        if _is_within(resolved, repository / directory):
            raise ValueError(f"Private tracking cannot be stored under repository/{directory}")
    if resolved == repository / "application_state.json":
        raise ValueError("Private tracking cannot be stored at the repository root")
    if _is_within(resolved, repository) and not _is_within(resolved, repository / "private"):
        try:
            relative = resolved.relative_to(repository)
            check = subprocess.run(
                ["git", "check-ignore", "--quiet", "--", str(relative)],
                cwd=repository, check=False, capture_output=True,
            )
        except OSError as error:
            raise ValueError("Cannot verify that the in-repository private-state path is Git-ignored") from error
        if check.returncode != 0:
            raise ValueError("An in-repository private-state path must be excluded by Git ignore rules")
    return resolved


class TrackingStore:
    def __init__(self, path: Path, root: Path):
        self.path = validate_private_state_path(path, root)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "jobs": {}}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload.get("jobs"), dict):
            raise ValueError("Private tracking file must contain a jobs object")
        return payload

    def set_status(self, job_id: str, state: str, note: str | None = None) -> dict[str, Any]:
        if state not in ALLOWED_STATES:
            raise ValueError(f"Invalid state {state!r}; allowed states: {', '.join(sorted(ALLOWED_STATES))}")
        if not job_id.strip():
            raise ValueError("Job ID cannot be empty")
        payload = self.load()
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        record = payload["jobs"].setdefault(job_id, {"history": []})
        event: dict[str, Any] = {"state": state, "timestamp": timestamp}
        if note:
            event["note"] = note
        record["state"] = state
        record["updated_at"] = timestamp
        record.setdefault("history", []).append(event)
        if note:
            record["note"] = note
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)
        return record
