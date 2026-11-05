"""Fail before remote tests unless an explicitly named CI-only database is configured."""

import os
import re

from sqlalchemy.engine import make_url

from app.db.config import DatabaseSettings


def main() -> None:
    expected = os.environ.get("ARCHMIND_CI_DATABASE_NAME", "")
    if not re.fullmatch(r"archmind_ci_[a-z0-9_]+", expected):
        raise SystemExit(
            "Set CI_ISOLATED_DATABASE_NAME to a dedicated archmind_ci_* database name."
        )
    try:
        url = make_url(os.environ["DATABASE_URL"])
        DatabaseSettings().sqlalchemy_url()
    except Exception:
        raise SystemExit("Configure a valid remote CI_ISOLATED_DATABASE_URL.") from None
    if url.database != expected:
        raise SystemExit(
            "Isolated database name does not match CI_ISOLATED_DATABASE_URL."
        )
    print("Dedicated remote PostgreSQL database name verified.")


if __name__ == "__main__":
    main()
