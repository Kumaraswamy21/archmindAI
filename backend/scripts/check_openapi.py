"""Validate and compare the tracked OpenAPI contract with the running app schema."""

import argparse
import json
from pathlib import Path

from openapi_spec_validator import validate

from app.main import create_app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write", action="store_true", help="Refresh the tracked contract."
    )
    args = parser.parse_args()
    tracked_path = Path(__file__).resolve().parents[2] / "docs" / "api" / "openapi.json"
    generated = create_app().openapi()
    validate(generated)
    if args.write:
        tracked_path.write_text(
            json.dumps(generated, indent=2, ensure_ascii=False) + "\n"
        )
        print(f"Updated {tracked_path} ({len(generated['paths'])} paths).")
        return
    tracked = json.loads(tracked_path.read_text())
    validate(tracked)
    if tracked != generated:
        raise SystemExit(
            "Tracked OpenAPI differs from the app. Run: python scripts/check_openapi.py --write"
        )
    print(f"OpenAPI valid and in sync ({len(generated['paths'])} paths).")


if __name__ == "__main__":
    main()
