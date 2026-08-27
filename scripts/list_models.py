"""List local models detected by the Apex AI model manager.

    python scripts/list_models.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from apex_ai.config.settings import load_settings  # noqa: E402
from apex_ai.models.manager import ModelManager  # noqa: E402
from apex_ai.security.files import human_size  # noqa: E402


def main() -> int:
    settings = load_settings()
    manager = ModelManager(settings)
    entries = manager.discover()

    print(f"Model directory: {settings.model_dir}")
    print(f"Configured model: {settings.model_path or '(none)'}")
    if not entries:
        print("\nNo models found.")
        print(f"Place .gguf files in {settings.model_dir} or set APEX_MODEL_PATH.")
        return 0

    print(f"\n{'NAME':<50} {'TYPE':<8} {'SIZE':>10}  {'STATUS':<16} ACTIVE")
    for entry in entries:
        active = "yes" if entry.is_active else ""
        print(
            f"{entry.name:<50} {entry.file_type:<8} {human_size(entry.size_bytes):>10}"
            f"  {entry.status:<16} {active}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
