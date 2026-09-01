"""YamlJobDefinitionSource: jobs/*.yaml → validated JobDefinition.

The Pydantic model is the schema; this adapter only handles files. A file
that fails validation raises with its path in the message — the catalog
turns that into a startup failure (fail fast, spec §8).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from atlas.jobs.domain.definition import JobDefinition


class JobFileError(Exception):
    def __init__(self, path: Path, cause: Exception) -> None:
        super().__init__(f"{path}: {cause}")
        self.path = path


class YamlJobDefinitionSource:
    def __init__(self, jobs_dir: Path) -> None:
        self._jobs_dir = jobs_dir

    def load_all(self) -> list[JobDefinition]:
        if not self._jobs_dir.is_dir():
            raise FileNotFoundError(f"jobs directory not found: {self._jobs_dir}")
        definitions: list[JobDefinition] = []
        for path in sorted(self._jobs_dir.glob("*.yaml")) + sorted(self._jobs_dir.glob("*.yml")):
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                definitions.append(JobDefinition.model_validate(raw))
            except Exception as exc:
                raise JobFileError(path, exc) from exc
        return definitions
