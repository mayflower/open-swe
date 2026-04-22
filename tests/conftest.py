from __future__ import annotations

import os

# Test-only opt-in for the repo-memory in-memory adapter. Production code
# refuses to start with the in-memory backend unless this flag is set, so the
# test harness declares it explicitly.
os.environ.setdefault("REPO_MEMORY_ALLOW_IN_MEMORY", "true")
