"""Legacy entry point.

The original file loaded a hard-coded model at import time and bypassed the
supported UI/provider configuration. Keep this command as a safe compatibility
alias for the application launcher.
"""

from ui import launch


if __name__ == "__main__":
    launch()
