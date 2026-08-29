"""Generate a tiny VALID GGUF model with random weights.

Purpose: end-to-end verification of the llama.cpp provider path in sandboxes
with no internet. The model has the llama architecture, 1 layer and 2 heads,
so llama.cpp can load it — output text will be gibberish (random weights).
NOT for production use; delete after verifying, or keep out of git
 (*.gguf is gitignored anyway).

    python scripts/make_tiny_gguf.py [output.gguf]
"""

from __future__ import annotations

import sys
from pathlib import Path

import gguf
import numpy as np

SPECIAL = ["<s>", "</s>", "<unk>"]
WORDS = [
    "the", "of", "and", "to", "in", "is", "a", "fever", "adults", "children",
    "body", "temperature", "treatment", "care", "medical", "help", "signs",
    "water", "burn", "cool", "minutes", "doctor", "documents", "answer",
    "question", "source", "evidence", "1", "2", "3", ".", ",", ":", "▁",
]
# SPM needs the 256 byte-fallback tokens to tokenize arbitrary text.
BYTE_TOKENS = [f"<0x{i:02X}>" for i in range(256)]

TOKENS = SPECIAL + BYTE_TOKENS + WORDS
N_TOKENS = len(TOKENS)
N_EMBD = 32
N_HEAD = 2
N_LAYER = 1
N_FF = 64


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("models/tiny-random-llama.gguf")
    out.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)

    # GGUFWriter records general.architecture from its ``arch`` argument.
    # Calling add_architecture() again duplicates that key on current gguf.
    writer = gguf.GGUFWriter(str(out), "llama")
    writer.add_block_count(N_LAYER)
    writer.add_context_length(256)
    writer.add_embedding_length(N_EMBD)
    writer.add_feed_forward_length(N_FF)
    writer.add_head_count(N_HEAD)
    writer.add_head_count_kv(N_HEAD)
    writer.add_layer_norm_rms_eps(1e-5)
    writer.add_rope_freq_base(10000.0)

    writer.add_tokenizer_model("llama")
    writer.add_token_list(TOKENS)
    writer.add_token_scores([-1e9] * 3 + [0.0] * (N_TOKENS - 3))
    writer.add_token_types(
        [gguf.TokenType.CONTROL, gguf.TokenType.UNKNOWN, gguf.TokenType.UNKNOWN]
        + [gguf.TokenType.BYTE] * 256
        + [gguf.TokenType.NORMAL] * len(WORDS)
    )
    writer.add_add_bos_token(True)
    writer.add_add_eos_token(False)
    writer.add_bos_token_id(0)
    writer.add_eos_token_id(1)

    def tensor(name, shape):
        writer.add_tensor(name, rng.standard_normal(shape, dtype=np.float32) * 0.05)

    tensor("token_embd.weight", (N_TOKENS, N_EMBD))
    tensor("output_norm.weight", (N_EMBD,))
    tensor("output.weight", (N_TOKENS, N_EMBD))
    for i in range(N_LAYER):
        p = f"blk.{i}"
        tensor(f"{p}.attn_norm.weight", (N_EMBD,))
        tensor(f"{p}.attn_q.weight", (N_EMBD, N_EMBD))
        tensor(f"{p}.attn_k.weight", (N_EMBD, N_EMBD))
        tensor(f"{p}.attn_v.weight", (N_EMBD, N_EMBD))
        tensor(f"{p}.attn_output.weight", (N_EMBD, N_EMBD))
        tensor(f"{p}.ffn_norm.weight", (N_EMBD,))
        tensor(f"{p}.ffn_gate.weight", (N_FF, N_EMBD))
        tensor(f"{p}.ffn_down.weight", (N_EMBD, N_FF))
        tensor(f"{p}.ffn_up.weight", (N_FF, N_EMBD))

    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()
    print(f"Wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
