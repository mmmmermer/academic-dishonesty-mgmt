#!/usr/bin/env python3
"""Phase-0 baseline checks for search behavior.

This script is read-only: it runs representative teacher/admin queries,
collects timings, and exports a JSON report for review.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pypinyin import Style, lazy_pinyin

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.database import DATABASE_URL, IS_SQLITE, db_session
from core.models import Blacklist
from core.search import (
    fetch_teacher_candidate_records,
    parse_teacher_search_inputs,
    search_teacher_records,
)
from views.components import build_blacklist_query


@dataclass
class TeacherCaseResult:
    case: str
    query: str
    candidates: int
    hits: int
    modes: list[str]
    ms: float


@dataclass
class AdminCaseResult:
    case: str
    name_filter: str
    sid_filter: str
    count: int
    ms: float


def _space_name(name: str) -> str:
    return " ".join(list(name.strip()))


def _to_pinyin_full(name: str) -> str:
    return "".join(lazy_pinyin(name, style=Style.NORMAL)).lower()


def _to_pinyin_abbr(name: str) -> str:
    return "".join(lazy_pinyin(name, style=Style.FIRST_LETTER)).lower()


def _run_teacher_cases(db: Any, n1: str, s1: str, n2: str) -> list[TeacherCaseResult]:
    spaced_n1 = _space_name(n1)
    pinyin_full = _to_pinyin_full(n1)
    pinyin_abbr = _to_pinyin_abbr(n1)

    cases = [
        ("name_exact", n1),
        ("name_spaced", spaced_n1),
        ("id_exact", s1),
        ("name_and_id", f"{n1} {s1}"),
        ("multi_newline", f"{n1}\n{n2}"),
        ("multi_comma", f"{n1},{n2}"),
        ("pinyin_full", pinyin_full),
        ("pinyin_abbr", pinyin_abbr),
        ("typo_probe", "张烜"),
        ("homophone_probe", "王方"),
    ]

    results: list[TeacherCaseResult] = []
    for label, query in cases:
        t0 = time.perf_counter()
        parsed = parse_teacher_search_inputs(query)
        candidates = fetch_teacher_candidate_records(db, parsed)
        records, modes = search_teacher_records(candidates, parsed)
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        results.append(
            TeacherCaseResult(
                case=label,
                query=query,
                candidates=len(candidates),
                hits=len(records),
                modes=sorted(list(modes)),
                ms=elapsed_ms,
            )
        )
    return results


def _run_admin_cases(db: Any, n1: str, s1: str, n2: str, s2: str) -> list[AdminCaseResult]:
    spaced_n1 = _space_name(n1)
    pinyin_full = _to_pinyin_full(n1)
    pinyin_abbr = _to_pinyin_abbr(n1)

    cases = [
        ("name_exact", n1, ""),
        ("name_spaced", spaced_n1, ""),
        ("name_partial", n1[:1], ""),
        ("name_pinyin_full", pinyin_full, ""),
        ("name_pinyin_abbr", pinyin_abbr, ""),
        ("name_multi_comma", f"{n1},{n2}", ""),
        ("sid_multi", "", f"{s1} {s2}"),
    ]

    results: list[AdminCaseResult] = []
    for label, name_filter, sid_filter in cases:
        t0 = time.perf_counter()
        query = build_blacklist_query(
            db,
            status=1,
            name_filter=name_filter,
            sid_filter=sid_filter,
            major_categories=[],
        )
        count = query.count()
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        results.append(
            AdminCaseResult(
                case=label,
                name_filter=name_filter,
                sid_filter=sid_filter,
                count=count,
                ms=elapsed_ms,
            )
        )
    return results


def generate_report() -> dict[str, Any]:
    report: dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "database_url": DATABASE_URL,
        "is_sqlite": bool(IS_SQLITE),
    }

    with db_session() as db:
        total = db.query(Blacklist).count()
        active_records = (
            db.query(Blacklist)
            .filter(Blacklist.status == 1)
            .order_by(Blacklist.id.asc())
            .all()
        )

        if len(active_records) < 2:
            raise RuntimeError("Need at least 2 active records for phase-0 baseline checks.")

        r1 = active_records[0]
        r2 = active_records[1]

        report["dataset"] = {
            "total_records": total,
            "active_records": len(active_records),
        }
        report["samples"] = {
            "name1": r1.name,
            "id1": r1.student_id,
            "name2": r2.name,
            "id2": r2.student_id,
            "name1_spaced": _space_name(r1.name),
            "name1_pinyin_full": _to_pinyin_full(r1.name),
            "name1_pinyin_abbr": _to_pinyin_abbr(r1.name),
        }

        report["teacher"] = [
            asdict(r)
            for r in _run_teacher_cases(
                db=db,
                n1=r1.name,
                s1=r1.student_id,
                n2=r2.name,
            )
        ]
        report["admin"] = [
            asdict(r)
            for r in _run_admin_cases(
                db=db,
                n1=r1.name,
                s1=r1.student_id,
                n2=r2.name,
                s2=r2.student_id,
            )
        ]

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run phase-0 search baseline checks.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write JSON report.",
    )
    args = parser.parse_args()

    report = generate_report()
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
