"""Fast foundation tests: deliberately do not import ingest.py or download models."""
import ast
from pathlib import Path


SOURCE = Path(__file__).parents[1] / "ingest.py"
_TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))


def load_function(name):
    node = next(n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
    namespace = {"Path": Path}
    exec(compile(ast.Module([node], type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace[name]


def test_safe_filename_removes_path_components():
    safe_filename = load_function("safe_filename")
    assert safe_filename("../../patient.pdf") == "patient.pdf"
    assert safe_filename(r"C:\\temp\\patient.pdf") == "patient.pdf"


def test_chunk_text_is_non_empty_and_bounded():
    chunk_text = load_function("chunk_text")
    chunks = chunk_text("one two three four five", chunk_size=10, overlap=2)
    assert chunks
    assert all(len(chunk) <= 10 for chunk in chunks)
    assert "" not in chunks


def test_chunk_text_handles_blank_input():
    assert load_function("chunk_text")("   \n\t") == []


def test_source_contract_documents_the_known_risks():
    foundation = (Path(__file__).parents[1] / "docs" / "FOUNDATION.md").read_text()
    assert "hard-coded absolute model path" in foundation
    assert "minimum similarity/distance policy" in foundation
    assert ".env.example" in foundation
