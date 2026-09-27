"""Shared core for the productivity journal: SQLite storage, config, week math, stats.

Used by both the CLI (journal.py) and the web server (server.py) so they always
agree on the data. Entries live in journal.db; legacy markdown files under
entries/ are imported automatically the first time they are seen.
"""

import json
import math
import os
import sqlite3
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(os.environ.get("JOURNAL_HOME") or Path(__file__).resolve().parent)
DB_PATH = ROOT / "journal.db"
ENTRIES_DIR = ROOT / "entries"
CONFIG_PATH = ROOT / "config.json"
MARKER_PATH = ROOT / ".last-auto-report"

DEFAULT_CONFIG = {
    "metrics": [
        {"key": "productivity", "label": "Productivity", "min": 1, "max": 10, "required": True},
        {"key": "energy", "label": "Energy", "min": 1, "max": 10},
        {"key": "focus_hours", "label": "Deep-work hours", "min": 0, "max": 24, "float": True},
        {"key": "sleep_hours", "label": "Sleep last night (hours)", "min": 0, "max": 24, "float": True},
        {"key": "exercise_minutes", "label": "Exercise (minutes)", "min": 0, "max": 1440},
    ],
    "prompts": [
        {"key": "wins", "label": "Top wins today"},
        {"key": "blockers", "label": "What slowed you down"},
        {"key": "tomorrow", "label": "#1 priority for tomorrow"},
    ],
}


def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        cfg.setdefault("metrics", DEFAULT_CONFIG["metrics"])
        cfg.setdefault("prompts", DEFAULT_CONFIG["prompts"])
        return cfg
    return DEFAULT_CONFIG


# ---------------------------------------------------------------- storage

def connect():
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "CREATE TABLE IF NOT EXISTS entries ("
        " date TEXT PRIMARY KEY,"
        " metrics TEXT NOT NULL DEFAULT '{}',"
        " sections TEXT NOT NULL DEFAULT '{}',"
        " updated_at TEXT NOT NULL)"
    )
    con.commit()
    _import_legacy_markdown(con)
    return con


def parse_markdown_entry(path):
    """Parse a legacy entries/YYYY-MM-DD.md file."""
    lines = path.read_text().splitlines()
    metrics, sections = {}, {}
    i = 0
    if lines and lines[0].strip() == "---":
        i = 1
        while i < len(lines) and lines[i].strip() != "---":
            if ":" in lines[i]:
                key, _, val = lines[i].partition(":")
                key, val = key.strip(), val.strip()
                if key != "date" and val:
                    try:
                        metrics[key] = int(val)
                    except ValueError:
                        try:
                            metrics[key] = float(val)
                        except ValueError:
                            pass
            i += 1
        i += 1
    heading, buf = None, []
    for line in lines[i:]:
        if line.startswith("## "):
            if heading is not None:
                sections[heading] = "\n".join(buf).strip()
            heading, buf = line[3:].strip(), []
        elif heading is not None:
            buf.append(line)
    if heading is not None:
        sections[heading] = "\n".join(buf).strip()
    return {"metrics": metrics, "sections": sections}


def _import_legacy_markdown(con):
    """One-way import: markdown entries not yet in the DB are added, never overwritten."""
    if not ENTRIES_DIR.exists():
        return
    have = {row[0] for row in con.execute("SELECT date FROM entries")}
    changed = False
    for path in sorted(ENTRIES_DIR.glob("????-??-??.md")):
        if path.stem in have:
            continue
        try:
            date.fromisoformat(path.stem)
        except ValueError:
            continue
        e = parse_markdown_entry(path)
        con.execute(
            "INSERT INTO entries (date, metrics, sections, updated_at) VALUES (?, ?, ?, ?)",
            (path.stem, json.dumps(e["metrics"]), json.dumps(e["sections"]),
             datetime.now().isoformat(timespec="seconds")),
        )
        changed = True
    if changed:
        con.commit()


def get_entry(con, d):
    row = con.execute("SELECT metrics, sections FROM entries WHERE date = ?",
                      (d.isoformat(),)).fetchone()
    if row is None:
        return None
    return {"metrics": json.loads(row[0]), "sections": json.loads(row[1])}


def upsert_entry(con, d, metrics, sections):
    metrics = {k: v for k, v in metrics.items() if v is not None}
    sections = {k: v for k, v in sections.items() if v}
    con.execute(
        "INSERT INTO entries (date, metrics, sections, updated_at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT(date) DO UPDATE SET metrics = excluded.metrics,"
        " sections = excluded.sections, updated_at = excluded.updated_at",
        (d.isoformat(), json.dumps(metrics), json.dumps(sections),
         datetime.now().isoformat(timespec="seconds")),
    )
    con.commit()
    git_sync(d.isoformat())


def git_sync(label):
    """Back up the journal: commit journal.db and push in the background.

    Best-effort — a save must never fail because git or the network did.
    Disable with "git_sync": false in config.json.
    """
    if not load_config().get("git_sync", True) or not (ROOT / ".git").exists():
        return
    try:
        subprocess.run(["git", "-C", str(ROOT), "add", "journal.db"],
                       capture_output=True, timeout=10)
        committed = subprocess.run(
            ["git", "-C", str(ROOT), "commit", "--quiet", "-m", f"journal {label}"],
            capture_output=True, timeout=10,
        ).returncode == 0
        if committed:
            subprocess.Popen(["git", "-C", str(ROOT), "push", "--quiet"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def all_entries(con):
    """Return {date: {'metrics': ..., 'sections': ...}} sorted by date."""
    result = {}
    for iso, m, s in con.execute("SELECT date, metrics, sections FROM entries ORDER BY date"):
        result[date.fromisoformat(iso)] = {"metrics": json.loads(m), "sections": json.loads(s)}
    return result


def coerce_metric(m, raw):
    """Validate a metric value (str or number). Returns the value, or raises ValueError."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{m['key']} must be a number")
    if not m.get("float"):
        val = int(round(val))
    lo, hi = m.get("min", 0), m.get("max", 10)
    if not lo <= val <= hi:
        raise ValueError(f"{m['key']} must be between {lo} and {hi}")
    return val


# ---------------------------------------------------------------- week math

def week_start(d):
    """Sunday that starts the week containing d (weeks run Sun–Sat)."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def default_report_week(today):
    """The week a report should show: on Sundays the just-completed week, else this week."""
    ws = week_start(today)
    return ws - timedelta(days=7) if today == ws else ws


def avg(values):
    return sum(values) / len(values) if values else None


def metric_values(entries, days, key):
    return [entries[d]["metrics"][key] for d in days
            if d in entries and key in entries[d]["metrics"]]


def week_avg(entries, ws, key="productivity"):
    return avg(metric_values(entries, [ws + timedelta(days=i) for i in range(7)], key))


def pearson(pairs):
    n = len(pairs)
    if n < 5:
        return None
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    cov = sum((x - mx) * (y - my) for x, y in pairs)
    vx = sum((x - mx) ** 2 for x, _ in pairs)
    vy = sum((y - my) ** 2 for _, y in pairs)
    if vx == 0 or vy == 0:
        return None
    return cov / math.sqrt(vx * vy)


def streaks(dates, today):
    current, d = 0, today
    if d not in dates:
        d -= timedelta(days=1)
    while d in dates:
        current += 1
        d -= timedelta(days=1)
    longest, run, prev = 0, 0, None
    for d in sorted(dates):
        run = run + 1 if prev == d - timedelta(days=1) else 1
        longest = max(longest, run)
        prev = d
    return current, longest


# ---------------------------------------------------------------- report data

def week_report_data(entries, ws, cfg, today):
    """Everything a weekly report needs, as plain data (shared by CLI and web)."""
    days = []
    for i in range(7):
        d = ws + timedelta(days=i)
        e = entries.get(d)
        p = e["metrics"].get("productivity") if e else None
        days.append({
            "date": d.isoformat(),
            "weekday": d.strftime("%a"),
            "productivity": p,
            "win": (e["sections"].get("Top wins today", "") if e else "").split("\n")[0],
            "future": d > today,
        })
    day_dates = [ws + timedelta(days=i) for i in range(7)]
    pvals = metric_values(entries, day_dates, "productivity")
    logged = [d for d in day_dates if d in entries and "productivity" in entries[d]["metrics"]]
    metrics = []
    for m in cfg["metrics"]:
        if m["key"] == "productivity":
            continue
        now = avg(metric_values(entries, day_dates, m["key"]))
        if now is None:
            continue
        metrics.append({
            "label": m["label"],
            "avg": round(now, 2),
            "prior": (lambda b: round(b, 2) if b is not None else None)(
                week_avg(entries, ws - timedelta(days=7), m["key"])),
        })
    best = max(logged, key=lambda d: entries[d]["metrics"]["productivity"], default=None)
    worst = min(logged, key=lambda d: entries[d]["metrics"]["productivity"], default=None)
    prior = week_avg(entries, ws - timedelta(days=7))
    return {
        "week_start": ws.isoformat(),
        "week_end": (ws + timedelta(days=6)).isoformat(),
        "days": days,
        "average": round(avg(pvals), 2) if pvals else None,
        "prior_average": round(prior, 2) if prior is not None else None,
        "logged": len(logged),
        "best": best.isoformat() if best else None,
        "worst": worst.isoformat() if worst else None,
        "metrics": metrics,
    }


def stats_data(entries, cfg, today):
    if not entries:
        return None
    all_dates = sorted(entries)
    pvals = [e["metrics"]["productivity"] for e in entries.values()
             if "productivity" in e["metrics"]]
    current, longest = streaks(set(entries), today)
    weekly, ws = [], week_start(all_dates[0])
    this_ws = week_start(today)
    while ws <= this_ws:
        w = week_avg(entries, ws)
        weekly.append({"week_start": ws.isoformat(),
                       "avg": round(w, 2) if w is not None else None,
                       "in_progress": ws == this_ws})
        ws += timedelta(days=7)
    correlations = []
    for m in cfg["metrics"]:
        if m["key"] == "productivity":
            continue
        pairs = [(e["metrics"][m["key"]], e["metrics"]["productivity"])
                 for e in entries.values()
                 if m["key"] in e["metrics"] and "productivity" in e["metrics"]]
        r = pearson(pairs)
        if r is None:
            continue
        strength = "strong" if abs(r) >= 0.6 else "moderate" if abs(r) >= 0.3 else "weak"
        correlations.append({"label": m["label"], "r": round(r, 2), "strength": strength})
    return {
        "first": all_dates[0].isoformat(),
        "last": all_dates[-1].isoformat(),
        "count": len(entries),
        "overall": round(avg(pvals), 2) if pvals else None,
        "current_streak": current,
        "longest_streak": longest,
        "weekly": weekly,
        "correlations": correlations,
    }
