"""STUB gate for the dry run only: stands in for tests/doom/trusted/run.sh. Checks that files exist; cwd = verify.py's exported candidate."""
import sys
from pathlib import Path

gate = sys.argv[1]
need = {"build": ["build.sh"], "m1": ["build.sh", "platform/dg_test_hooks.c", "web/index.html"], "final": ["build.sh", "platform/dg_test_hooks.c"]}[gate]
bad = [f for f in need if not Path(f).is_file()]
if gate == "final" and "final-ready" not in Path("web/index.html").read_text():
    bad.append("web/index.html:final-ready")
print("ASSERT STUB.%s %s %s" % (gate, "FAIL" if bad else "PASS", bad))
sys.exit(1 if bad else 0)
