#!/usr/bin/env python3
"""Read evcc's SQLite config to see where each meter reads from.

This is the authoritative answer to 'what does evcc call grid/pv/battery' -
guessing from entity names is not enough when a sub-meter is involved.
"""
import sqlite3
import json
from pathlib import Path

DB = "/home/user/evcc/data/evcc.db"


def main():
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    con.row_factory = sqlite3.Row
    tables = [r[0] for r in con.execute(
        "select name from sqlite_master where type='table' order by name")]
    print("tables:", tables)

    for t in tables:
        cols = [d[1] for d in con.execute("pragma table_info(%s)" % t)]
        rows = con.execute("select * from %s" % t).fetchall()
        print("\n== %s (%d rows) cols=%s" % (t, len(rows), cols))
        for r in rows[:12]:
            d = dict(r)
            line = json.dumps(d, ensure_ascii=False, default=str)
            if len(line) > 1200:
                line = line[:1200] + " ...(truncated)"
            print("  ", line)


if __name__ == "__main__":
    main()