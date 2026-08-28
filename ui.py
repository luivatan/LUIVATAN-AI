"""Apex AI chat-first web application entry point.

Run ``python ui.py`` and open http://127.0.0.1:7860. The previous Gradio
interface remains available as ``apex_ai.ui.gradio_app`` for compatibility.
"""

from apex_ai.web import launch

if __name__ == "__main__":
    launch()
