"""Discovery and lightweight validation of local GGUF models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from apex_ai.core.errors import ModelNotFoundError
from apex_ai.security.files import human_size


@dataclass(frozen=True)
class ModelEntry:
    name: str
    path: Path
    model_type: str
    size: str
    provider: str
    status: str
    active: bool
    loadable: bool

    def row(self) -> list:
        return [
            self.name,
            self.model_type,
            self.size,
            self.provider,
            self.status,
            "yes" if self.active else "",
        ]


class ModelManager:
    def __init__(self, settings) -> None:
        self.settings = settings

    @staticmethod
    def _is_gguf(path: Path) -> bool:
        try:
            with path.open("rb") as handle:
                return handle.read(4) == b"GGUF"
        except OSError:
            return False

    def discover(self) -> list[ModelEntry]:
        candidates: dict[Path, None] = {}
        directory = Path(self.settings.model_dir)
        if directory.is_dir():
            for path in directory.glob("*.gguf"):
                if path.is_file():
                    candidates[path.resolve()] = None
        if self.settings.model_path:
            configured = Path(self.settings.model_path).expanduser()
            if configured.is_file():
                candidates[configured.resolve()] = None

        active = (
            Path(self.settings.model_path).expanduser().resolve()
            if self.settings.model_path
            else None
        )
        entries = []
        for path in candidates:
            valid = self._is_gguf(path)
            entries.append(
                ModelEntry(
                    name=path.name,
                    path=path,
                    model_type="GGUF",
                    size=human_size(path.stat().st_size),
                    provider="llama.cpp",
                    status="ready" if valid else "unknown format",
                    active=active == path,
                    loadable=valid,
                )
            )
        return sorted(entries, key=lambda entry: entry.name.lower())

    def resolve(self, name_or_path: str) -> Path:
        requested = Path(name_or_path).expanduser()
        candidate = requested if requested.is_absolute() else Path(self.settings.model_dir) / requested
        if not candidate.is_file():
            matching = next(
                (entry.path for entry in self.discover() if entry.name == requested.name), None
            )
            candidate = matching or candidate
        if not candidate.is_file():
            choices = [entry.name for entry in self.discover() if entry.loadable]
            alternatives = "\nAvailable models:\n" + "\n".join(
                f"  - {name}" for name in choices
            ) if choices else ""
            raise ModelNotFoundError(
                what=f"The model `{name_or_path}` was not found.",
                why=f"Apex AI searched the configured model directory: {self.settings.model_dir}",
                fix=(
                    "Put a valid .gguf file in APEX_MODEL_DIR, select one shown in the "
                    "interface, or set APEX_MODEL_PATH to an exact file."
                    + alternatives
                ),
            )
        candidate = candidate.resolve()
        if candidate.suffix.lower() != ".gguf" or not self._is_gguf(candidate):
            raise ModelNotFoundError(
                what=f"`{candidate.name}` is not a valid GGUF model file.",
                why="The required GGUF magic header is missing.",
                fix="Choose a complete .gguf model downloaded from a trusted source.",
            )
        return candidate
