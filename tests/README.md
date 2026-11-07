# Tests

| Path | What it checks |
|---|---|
| `test_loop_*.py`, `test_sandbox_verify.py` | The loop itself, with scripted fake agents (`fake_*.py`). Run: `python3 -m unittest discover -s tests -p "test_*.py"` |
| `demo_*.py` | Runnable demonstrations of each loop stage |
| `doom/ACCEPTANCE.md` | The acceptance contract: criteria, gates, negative controls |
| `doom/trusted/` | Frozen browser tests (Playwright). Hashes in `doom/frozen.json`. Run inside the toolchain image: `bash tests/doom/trusted/run.sh GATE` |
| `doom/QA-PROVENANCE.md` | How the trusted tests were written, reviewed and recovered |
