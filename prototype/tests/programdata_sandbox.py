"""Point %PROGRAMDATA% at a throwaway directory for this test process (import side effect).

Agent and Setup code keep their state under %PROGRAMDATA%\\WatchLog: setup.log
("[recorder-test]" lines), Secrets\\runtime-health.json, agent_state.json, the DPAPI blobs,
spools. A test that reaches that code without redirecting PROGRAMDATA writes into the real
directory of whatever PC runs it, which on a site PC is the live installation.

conftest.py imports this module, so every pytest run (including a test file whose __main__
hands over to pytest.main) is covered before any test module is imported. A test file that is
also run as a plain script (unittest.main or its own runner) imports it first:

    import programdata_sandbox  # noqa: F401  (before any agent import)

Not a test module itself. Idempotent per process.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile

_MARKER = "WATCHLOG_TEST_PROGRAMDATA"
_ORIGINAL = "WATCHLOG_TEST_ORIGINAL_PROGRAMDATA"
# What PROGRAMDATA was before any sandbox replaced it (inherited by child processes): the
# directory tests must never touch.
ORIGINAL_PROGRAMDATA = (os.environ.get(_ORIGINAL)
                        or os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
os.environ[_ORIGINAL] = ORIGINAL_PROGRAMDATA


def sandbox_root() -> str:
    """Create (once per process) and activate the throwaway PROGRAMDATA; return its path."""
    current = os.environ.get(_MARKER)
    if current and os.environ.get("PROGRAMDATA") == current and os.path.isdir(current):
        return current
    root = tempfile.mkdtemp(prefix="wl-test-programdata-")
    os.environ["PROGRAMDATA"] = root
    os.environ[_MARKER] = root
    atexit.register(shutil.rmtree, root, True)
    return root


ROOT = sandbox_root()
