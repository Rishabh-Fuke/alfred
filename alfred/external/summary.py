"""Collect the outside-benchmark summaries under a folder into one markdown report.

    python -m alfred.external.summary runs/external --out runs/external/report.md
"""
from __future__ import annotations

import argparse
from pathlib import Path

ORDER = ("when2call", "bfcl", "agentdojo")


def collect(root: Path) -> str:
    found = sorted(root.glob("*/summary.md"), key=lambda p: (
        next((i for i, k in enumerate(ORDER) if p.parent.name.startswith(k)), len(ORDER)), p.parent.name))
    parts = ["# Outside benchmarks", "",
             "Benchmarks Alfred never trains on. Rates have 95% bootstrap intervals over items in brackets.", ""]
    parts += [p.read_text().strip() + "\n" for p in found]
    return "\n".join(parts) if found else "# Outside benchmarks\n\nNo results yet.\n"


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m alfred.external.summary", description=__doc__.split("\n")[0])
    p.add_argument("root")
    p.add_argument("--out", default="")
    a = p.parse_args(argv)
    text = collect(Path(a.root))
    if a.out:
        Path(a.out).write_text(text)
    print(text)


if __name__ == "__main__":
    main()
