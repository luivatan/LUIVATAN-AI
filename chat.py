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

from apex_ai.core.errors import UNEXPECTED_ERROR_MESSAGE, ApexError
from apex_ai.core.logging import get_logger
from apex_ai.runtime import build_services

log = get_logger("cli")


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
            print(error.public_message())
            return 1
        except Exception:
            log.exception("One-shot chat failed")
            print(UNEXPECTED_ERROR_MESSAGE)
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
            try:
                services.memory.clear()
                print("Conversation memory cleared.")
            except ApexError as error:
                print("\n" + error.public_message())
            except Exception:
                log.exception("Conversation-memory clear failed")
                print("\n" + UNEXPECTED_ERROR_MESSAGE)
            continue
        if not question:
            continue

        try:
            result = services.engine.ask(question)
            print("\n" + result.answer)
            if result.sources_block:
                print("\n" + result.sources_block)
        except ApexError as error:
            print("\n" + error.public_message())
        except Exception:
            log.exception("Interactive chat failed")
            print("\n" + UNEXPECTED_ERROR_MESSAGE)


if __name__ == "__main__":
    raise SystemExit(main())
