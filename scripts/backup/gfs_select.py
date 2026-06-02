#!/usr/bin/env python3
"""FEATURE-024: grandfather-father-son retention selector.

Reads candidate backup names (one per line) on stdin. Names must contain
``db_YYYYMMDD_HHMMSS.sqlite3`` (basename, full path, or gs:// URL all work).
Prints, one per line, the names to PRUNE (delete) — i.e. everything NOT in the
keep-set:

    keep = newest snapshot for each of the last DAILY_KEEP distinct days
         + newest snapshot for each of the last WEEKLY_KEEP distinct ISO weeks

Defaults: 7 daily + 4 weekly (override via env DAILY_KEEP / WEEKLY_KEEP).
Names that don't match the pattern (e.g. pre-migrate-*.sqlite3) are ignored
and never pruned. Portable: pure stdlib, no hardcoded paths.
"""
import os
import re
import sys
from datetime import date

DAILY_KEEP = int(os.environ.get("DAILY_KEEP", "7"))
WEEKLY_KEEP = int(os.environ.get("WEEKLY_KEEP", "4"))
PATTERN = re.compile(r"db_(\d{8})_(\d{6})\.sqlite3")


def main():
    items = []  # (name, ymd, hms)
    for line in sys.stdin:
        name = line.strip()
        if not name:
            continue
        m = PATTERN.search(name)
        if m:
            items.append((name, m.group(1), m.group(2)))

    # Newest first by (date, time).
    items.sort(key=lambda t: (t[1], t[2]), reverse=True)

    keep = set()

    # Daily: newest snapshot per day, most recent DAILY_KEEP days.
    day_newest, day_order = {}, []
    for name, ymd, _ in items:
        if ymd not in day_newest:
            day_newest[ymd] = name
            day_order.append(ymd)
    for ymd in day_order[:DAILY_KEEP]:
        keep.add(day_newest[ymd])

    # Weekly: newest snapshot per ISO week, most recent WEEKLY_KEEP weeks.
    week_newest = {}
    for name, ymd, _ in items:
        d = date(int(ymd[0:4]), int(ymd[4:6]), int(ymd[6:8]))
        iso_year, iso_week, _ = d.isocalendar()
        key = "%04d%02d" % (iso_year, iso_week)
        if key not in week_newest:  # items already newest-first
            week_newest[key] = name
    for key in sorted(week_newest, reverse=True)[:WEEKLY_KEEP]:
        keep.add(week_newest[key])

    for name, _, _ in items:
        if name not in keep:
            print(name)


if __name__ == "__main__":
    main()
