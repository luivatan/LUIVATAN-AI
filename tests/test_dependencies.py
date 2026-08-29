"""Dependency-manifest and development-tool compatibility tests."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

PROJECT_ROOT = Path(__file__).resolve().parents[1]

RUNTIME_DEPENDENCIES = {
    "chromadb",
    "fastapi",
    "gradio",
    "huggingface-hub",
    "llama-cpp-python",
    "pydantic",
    "pypdf",
    "python-dotenv",
    "python-multipart",
    "rank-bm25",
    "requests",
    "sentence-transformers",
    "starlette",
    "torch",
    "transformers",
    "uvicorn",
}

DEVELOPMENT_DEPENDENCIES = {
    "fpdf2",
    "gguf",
    "httpx2",
    "numpy",
    "packaging",
    "pytest",
}

IMPORT_DISTRIBUTIONS = {
    "chromadb": "chromadb",
    "dotenv": "python-dotenv",
    "fastapi": "fastapi",
    "gradio": "gradio",
    "huggingface_hub": "huggingface-hub",
    "llama_cpp": "llama-cpp-python",
    "pydantic": "pydantic",
    "pypdf": "pypdf",
    "rank_bm25": "rank-bm25",
    "requests": "requests",
    "sentence_transformers": "sentence-transformers",
    "starlette": "starlette",
    "torch": "torch",
    "transformers": "transformers",
    "uvicorn": "uvicorn",
}


def _requirements(filename: str) -> list[Requirement]:
    parsed: list[Requirement] = []
    for raw_line in (PROJECT_ROOT / filename).read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            parsed.append(Requirement(line))
    return parsed


def _names(requirements: list[Requirement]) -> set[str]:
    return {canonicalize_name(requirement.name) for requirement in requirements}


def test_dependency_manifests_are_parseable_unique_and_classified():
    runtime = _requirements("requirements.txt")
    development = _requirements("requirements-dev.txt")

    assert _names(runtime) == RUNTIME_DEPENDENCIES
    assert _names(development) == DEVELOPMENT_DEPENDENCIES
    assert len(runtime) == len(_names(runtime))
    assert len(development) == len(_names(development))
    assert _names(runtime).isdisjoint(_names(development))


def test_application_imports_have_declared_distribution_owners():
    imported: set[str] = set()
    for path in (PROJECT_ROOT / "apex_ai").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])

    third_party = imported - sys.stdlib_module_names - {"apex_ai"}
    assert third_party == set(IMPORT_DISTRIBUTIONS)
    assert set(IMPORT_DISTRIBUTIONS.values()).issubset(RUNTIME_DEPENDENCIES)


def test_direct_dependencies_have_review_boundaries():
    for requirement in _requirements("requirements.txt") + _requirements(
        "requirements-dev.txt"
    ):
        assert any(
            specifier.operator == "<" for specifier in requirement.specifier
        ), f"{requirement.name} needs a reviewed upper compatibility boundary"


def test_tiny_gguf_development_tool_matches_declared_gguf_api(tmp_path):
    output = tmp_path / "tiny.gguf"
    completed = subprocess.run(
        [sys.executable, "scripts/make_tiny_gguf.py", str(output)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert output.read_bytes()[:4] == b"GGUF"
    assert "Duplicated key" not in completed.stderr
