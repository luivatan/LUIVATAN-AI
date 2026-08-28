"""Launch the preserved Gradio interface.

The main entry point is now the custom chat-first web app (``python ui.py``).
This compatibility command is retained for users who depend on the previous tabs.
"""

from apex_ai.ui.gradio_app import launch

if __name__ == "__main__":
    launch()
