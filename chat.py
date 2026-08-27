"""Terminal chat REPL for Apex AI (replaces the old hardcoded-path chat.py).

Usage:
    python chat.py                 # interactive question loop
    python chat.py -q "question"   # one-shot question

The active LLM comes from configuration (APEX_MODEL_PATH / APEX_MODEL_DIR /
APEX_LLM_PROVIDER) — never a hardcoded path. Type 'exit' to quit, 'sources'
to toggle citation printing, 'clear' to wipe conversation memory.
"""

from __future__ import annotations

import argparse
import sys

from apex_ai.core.errors import ApexError
from apex_ai.runtime import build_services


def main() -> int:
    parser = argparse.ArgumentParser(description="Apex AI terminal chat")
    parser.add_argument("-q", "--question", type=str, default=None, help="one-shot question")
    args = parser.parse_args()

    services = build_services(quiet_llm=False)
    if services.startup_error:
        print(services.startup_error)
        return 1

    if args.question:
        try:
            result = services.engine.ask(args.question)
            print(result.answer)
            if result.sources_block:
                print("\n" + result.sources_block)
            return 0
        except ApexError as error:
            print(error.user_message())
            return 1

    print("Apex AI terminal chat — 'exit' to quit, 'clear' to reset memory.")
    while True:
        try:
            question = input("\nAsk a question: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if question.lower() in {"exit", "quit"}:
            return 0
        if question.lower() == "clear":
            services.memory.clear()
            print("Conversation memory cleared.")
            continue
        if not question:
            continue

        try:
            result = services.engine.ask(question)
            print("\n" + result.answer)
            if result.sources_block:
                print("\n" + result.sources_block)
        except ApexError as error:
            print("\n" + error.user_message())
        except Exception as error:  # unexpected — log has the traceback
            print(f"\nUnexpected error: {error}\nSee logs/apex.log for details.")


if __name__ == "__main__":
    raise SystemExit(main())
