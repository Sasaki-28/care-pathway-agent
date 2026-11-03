import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agents"))
sys.path.insert(0, str(ROOT / "tests"))

# Offline tests: use the scripted model and a throwaway database.
os.environ["CARE_AGENT_MODEL"] = "scripted-model"
os.environ.setdefault("SESSION_DB_URI", f"sqlite+aiosqlite:///{(ROOT / 'data' / 'test_sessions.db').as_posix()}")

import scripted_llm  # noqa: E402,F401  (registers the scripted model)
