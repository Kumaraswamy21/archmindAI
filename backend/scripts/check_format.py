"""Require formatting for new/edited Python files while ratcheting a legacy baseline."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "format-baseline.json"


def python_files() -> list[Path]:
    return sorted(
        path
        for folder in ("app", "tests", "scripts")
        for path in (ROOT / folder).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-baseline", action="store_true")
    args = parser.parse_args()
    files = python_files()
    if args.refresh_baseline:
        baseline = {}
        for path in files:
            if (
                subprocess.run(
                    ["ruff", "format", "--check", str(path)],
                    capture_output=True,
                    check=False,
                ).returncode
                == 1
            ):
                baseline[str(path.relative_to(ROOT))] = digest(path)
        BASELINE.write_text(json.dumps(baseline, indent=2) + "\n")
        print(f"Recorded {len(baseline)} unchanged legacy files.")
        return
    baseline = json.loads(BASELINE.read_text())
    to_check = [
        path
        for path in files
        if baseline.get(str(path.relative_to(ROOT))) != digest(path)
    ]
    if to_check:
        subprocess.run(
            ["ruff", "format", "--check", *(str(path) for path in to_check)], check=True
        )
    print(
        f"Formatting gate passed: {len(to_check)} checked, {len(files) - len(to_check)} unchanged legacy files."
    )


if __name__ == "__main__":
    main()
