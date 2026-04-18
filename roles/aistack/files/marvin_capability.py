#!/usr/bin/env python3
"""marvin_capability.py — Detect and write the Marvin capability profile.

Queries the Ollama API for the active model's parameter count and context window,
maps them to a capability tier, and rewrites the "## Capability Profile" section
of the deployed TOOLS.md.

Run by Ansible after every workspace sync (--hardware provided via role var).
Can also be run by Marvin on demand to verify or regenerate the profile.

Usage:
    python3 /home/openclaw/marvin_capability.py
    python3 /home/openclaw/marvin_capability.py --hardware "GTX 1060 6GB"
    python3 /home/openclaw/marvin_capability.py --dry-run

Exit codes:
    0  Profile written (or --dry-run succeeded)
    1  Could not reach Ollama API
    2  TOOLS.md not found or Capability Profile section missing

# ── Tier Thresholds ────────────────────────────────────────────────────────────
#
#   SMALL   params < 30B  OR  context_window < 16384
#   MEDIUM  params >= 30B AND context_window >= 16384 AND params < 70B
#   LARGE   params >= 70B AND context_window >= 32768
#
# These thresholds reflect the per-domain scaffolding matrix in
# workspace/skills/scaffold/SKILL.md.  Update both if you change thresholds.
#
# ── Detection flow ─────────────────────────────────────────────────────────────
#
#   1. GET http://localhost:11434/api/tags  →  list running/available models,
#      pick the first loaded model (or the one matching LITELLM_LOCAL_MODEL env).
#
#   2. POST http://localhost:11434/api/show {"name": <model>}  →  model metadata.
#      modelinfo["general.parameter_count"] gives exact param count as int.
#      modelinfo key "llama.context_length" or similar gives native context size.
#      Fallback: parse parameter count from model name tag (e.g. "llama3.1:8b" → 8).
#
#   3. Determine tier from thresholds above.
#
#   4. Read TOOLS_MD_PATH, find "## Capability Profile" section (ends at next
#      "---" or "##" header), replace with freshly rendered profile block.
#
#   5. Write TOOLS_MD_PATH back (unless --dry-run, which prints to stdout).
#
# ── TOOLS.md section format ────────────────────────────────────────────────────
#
# The rendered block must match this template exactly so future runs can find
# and replace it reliably.  Do not change the header or trailing separator.
#
#   ## Capability Profile
#
#   | Signal         | Value             |
#   |----------------|-------------------|
#   | Model          | {model}           |
#   | Parameters     | {params_display}  |
#   | Context window | {ctx_display}     |
#   | Hardware       | {hardware}        |
#   | **Tier**       | **{tier}**        |
#
#   Tier thresholds: **SMALL** < 30B params or < 16K ctx · **MEDIUM** ≥ 30B + 16K · **LARGE** ≥ 70B + 32K
#
#   Updated by Ansible on model changes. Run `python3 /home/openclaw/marvin_capability.py` to regenerate.
#
# ── Ansible integration ────────────────────────────────────────────────────────
#
#   This script is deployed and invoked from roles/aistack/tasks/openclaw.yml
#   immediately after "Sync OpenClaw workspace files".  The --hardware arg is
#   supplied from the Ansible var `aistack_hardware_label` (defined in
#   roles/aistack/defaults/main.yml).  Example task stub (TODO: implement):
#
#     - name: Deploy marvin_capability.py
#       ansible.builtin.copy:
#         src: marvin_capability.py
#         dest: /home/openclaw/marvin_capability.py
#         mode: "0755"
#         owner: openclaw
#         group: openclaw
#
#     - name: Update TOOLS.md capability profile
#       ansible.builtin.command:
#         cmd: >
#           python3 /home/openclaw/marvin_capability.py
#           --hardware "{{ aistack_hardware_label }}"
#       become: yes
#       become_user: openclaw
#       register: capability_result
#       changed_when: "'updated' in capability_result.stdout"
#
# ── TODO: implement ────────────────────────────────────────────────────────────
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

OLLAMA_API = "http://localhost:11434"
TOOLS_MD_PATH = Path("/home/openclaw/.openclaw/workspace/TOOLS.md")

# Tier thresholds — keep in sync with scaffold/SKILL.md
TIER_LARGE_PARAMS  = 70_000_000_000
TIER_LARGE_CTX     = 32_768
TIER_MEDIUM_PARAMS = 30_000_000_000
TIER_MEDIUM_CTX    = 16_384


def get_active_model() -> str:
    """Return the name of the currently loaded/default Ollama model.

    TODO: implement
    - GET {OLLAMA_API}/api/tags
    - If LITELLM_LOCAL_MODEL env var is set, match against it (strip "ollama/" prefix)
    - Otherwise return the first model in the list
    - Raise RuntimeError if API is unreachable
    """
    raise NotImplementedError


def get_model_info(model: str) -> dict:
    """Return parameter count and native context length for a model.

    TODO: implement
    - POST {OLLAMA_API}/api/show with {"name": model}
    - Extract modelinfo["general.parameter_count"] (int) as params
    - Extract context length from modelinfo (key varies by model family —
      try "llama.context_length", "context_length", num_ctx from options)
    - Fallback: parse params from model name tag (e.g. "8b" → 8e9, "70b" → 70e9)
    - Return {"params": int, "ctx": int, "model": str}
    """
    raise NotImplementedError


def determine_tier(params: int, ctx: int) -> str:
    """Map (params, ctx) to a tier string.

    TODO: implement per thresholds at top of file
    Returns "SMALL", "MEDIUM", or "LARGE"
    """
    raise NotImplementedError


def format_profile_section(model: str, params: int, ctx: int,
                            tier: str, hardware: str) -> str:
    """Render the TOOLS.md Capability Profile section as a string.

    TODO: implement
    - params_display: format as "8B", "70B", "3.5B" etc (divide by 1e9, round sensibly)
    - ctx_display: format as "8,192 tokens", "32,768 tokens" etc
    - Use the exact template from the module docstring
    """
    raise NotImplementedError


def update_tools_md(section: str, dry_run: bool = False) -> bool:
    """Find and replace the Capability Profile section in TOOLS.md.

    TODO: implement
    - Read TOOLS_MD_PATH
    - Find the block starting with "## Capability Profile\n" and ending just
      before the next "^---" or "^## " line (inclusive of trailing blank lines)
    - Replace with section + "\n---\n"
    - If dry_run: print new content to stdout, return True
    - Otherwise: write back to TOOLS_MD_PATH, return True if changed
    - Return False if section not found (caller should exit 2)
    """
    raise NotImplementedError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--hardware", default=None,
                        help="Hardware label for profile table (e.g. 'GTX 1060 6GB'). "
                             "If omitted, preserves the existing value in TOOLS.md.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the updated TOOLS.md section without writing.")
    args = parser.parse_args()

    # TODO: wire up implemented functions and remove this stub return
    print("marvin_capability.py: not yet implemented", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
