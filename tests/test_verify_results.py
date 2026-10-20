"""Every headline number in RESULTS.md and the README recomputes from the files committed in results/."""
import subprocess
import sys
from pathlib import Path


def test_headline_results_recompute_from_committed_files():
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, str(root / "scripts/verify_results.py")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
