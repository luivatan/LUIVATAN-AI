"""Apex AI entry point (Gradio UI).

Backward-compatible shim: the old project launched with `python ui.py` or
`python ingest.py` — both still work and now start Apex AI.
"""

from apex_ai.ui import launch

if __name__ == "__main__":
    launch()
