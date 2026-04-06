#!/usr/bin/env python3
"""Apply phase-1 PostgreSQL schema SQL with safety checks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.database import DATABASE_URL, engine  # noqa: E402


DEFAULT_SQL = PROJECT_ROOT / "deploy" / "sql" / "phase1_postgres_search_schema.sql"


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply phase-1 PostgreSQL schema.")
    parser.add_argument(
        "--sql",
        type=Path,
        default=DEFAULT_SQL,
        help="SQL file path. Defaults to deploy/sql/phase1_postgres_search_schema.sql",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate environment and SQL file without executing statements.",
    )
    args = parser.parse_args()

    if "postgresql" not in DATABASE_URL.lower():
        print(f"[ERROR] DATABASE_URL is not PostgreSQL: {DATABASE_URL}")
        return 2

    sql_path = args.sql.resolve()
    if not sql_path.exists():
        print(f"[ERROR] SQL file not found: {sql_path}")
        return 2

    sql_text = sql_path.read_text(encoding="utf-8")
    if not sql_text.strip():
        print(f"[ERROR] SQL file is empty: {sql_path}")
        return 2

    if args.dry_run:
        print(f"[DRY-RUN] Environment OK. SQL ready: {sql_path}")
        return 0

    with engine.begin() as conn:
        conn.execute(text(sql_text))

    print(f"[OK] Applied schema SQL: {sql_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

