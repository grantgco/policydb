"""Pytest bootstrap for this worktree.

The venv typically has ``policydb`` installed from the main repo path
(``~/Documents/Projects/policydb/src``). When tests run inside a worktree,
that installed copy wins over the worktree code unless we put the worktree
``src`` first on ``sys.path``. This conftest does exactly that, before any
test module is imported, so every test exercises the worktree's code.
"""
from __future__ import annotations

import sys
from pathlib import Path

_WORKTREE_SRC = Path(__file__).resolve().parent.parent / "src"
if _WORKTREE_SRC.is_dir():
    p = str(_WORKTREE_SRC)
    if p in sys.path:
        sys.path.remove(p)
    sys.path.insert(0, p)
