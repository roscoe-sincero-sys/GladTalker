"""GladTalker Studio backend (Cast -> Script -> Roll).

Phase 1 of issue #11: a local FastAPI + WebSocket engine that the Studio frontend (and the
optional ``--web`` mode) drives. Heavy dependencies (torch, coqui-tts, facexlib, SadTalker) are
imported lazily so the API can start, report hardware/model status, and parse scripts quickly.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
