#!/usr/bin/env python3
"""Phase-1 backfill for search helper fields.

This script fills `name_norm`, `name_pinyin_full`, and `name_abbr`
when these columns exist in `blacklist`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pypinyin import Style, lazy_pinyin
from sqlalchemy import inspect, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.database import engine  # noqa: E402


def normalize_name(name: str | None) -> str:
    return "".join((name or "").split())


def to_pinyin_full(name_norm: str) -> str:
    if not name_norm:
        return ""
    return "".join(lazy_pinyin(name_norm, style=Style.NORMAL)).lower()


def to_pinyin_abbr(name_norm: str) -> str:
    if not name_norm:
        return ""
    return "".join(lazy_pinyin(name_norm, style=Style.FIRST_LETTER)).lower()


def validate_columns() -> None:
    inspector = inspect(engine)
    names = {c["name"] for c in inspector.get_columns("blacklist")}
    required = {"name_norm", "name_pinyin_full", "name_abbr"}
    missing = sorted(required - names)
    if missing:
        raise RuntimeError(
            "Missing columns in blacklist: "
            + ", ".join(missing)
            + ". Apply phase1_postgres_search_schema.sql first."
        )


def run_backfill(batch_size: int, dry_run: bool) -> tuple[int, int]:
    updated = 0
    scanned = 0

    select_sql = text(
        """
        SELECT id, name, name_norm, name_pinyin_full, name_abbr
        FROM blacklist
        ORDER BY id ASC
        """
    )
    update_sql = text(
        """
        UPDATE blacklist
        SET name_norm = :name_norm,
            name_pinyin_full = :name_pinyin_full,
            name_abbr = :name_abbr
        WHERE id = :id
        """
    )

    with engine.begin() as conn:
        rows = conn.execute(select_sql).mappings().all()
        for row in rows:
            scanned += 1
            name_norm = normalize_name(row["name"])
            name_pinyin_full = to_pinyin_full(name_norm)
            name_abbr = to_pinyin_abbr(name_norm)

            changed = (
                (row["name_norm"] or "") != name_norm
                or (row["name_pinyin_full"] or "") != name_pinyin_full
                or (row["name_abbr"] or "") != name_abbr
            )
            if not changed:
                continue

            updated += 1
            if not dry_run:
                conn.execute(
                    update_sql,
                    {
                        "id": row["id"],
                        "name_norm": name_norm,
                        "name_pinyin_full": name_pinyin_full,
                        "name_abbr": name_abbr,
                    },
                )

            if batch_size > 0 and updated % batch_size == 0:
                # Transaction is managed by engine.begin(); this keeps progress visible.
                pass

    return scanned, updated


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill search helper fields.")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true", help="Do not write updates.")
    args = parser.parse_args()

    try:
        validate_columns()
    except RuntimeError as exc:
        print(f"[ERROR] {exc}")
        return 2
    scanned, updated = run_backfill(batch_size=args.batch_size, dry_run=args.dry_run)

    mode = "DRY-RUN" if args.dry_run else "WRITE"
    print(f"[{mode}] scanned={scanned}, would_update_or_updated={updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
