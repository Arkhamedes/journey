#!/usr/bin/env python3
"""Daily productivity journal.

Journal every day, rate your productivity out of 10, and get weekly
reports (ready Sunday morning) with stats, trends, and streaks.

Usage:
  journal.py                 start (or edit) today's entry interactively
  journal.py new --date D    write an entry for another day
  journal.py report          weekly report (on Sundays: the completed week)
  journal.py stats           all-time trends, streaks, and correlations
  journal.py log             list recent entries
  journal.py show DATE       print one entry
  journal.py serve           run the web UI at http://127.0.0.1:8765

Non-interactive entry (for scripting):
  journal.py new --set productivity=8 --set sleep_hours=7.5 --notes "..."

Data lives in journal.db (SQLite). Legacy markdown files in entries/ are
imported automatically. Metrics and prompts are configurable in config.json.
"""

import argparse
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jcore
from jcore import (MARKER_PATH, avg, default_report_week, load_config,
                   metric_values, streaks, week_avg, week_start)

SPARK = "▁▂▃▄▅▆▇█"
USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(code, text):
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else str(text)


def bold(t):
    return c("1", t)


def dim(t):
    return c("2", t)


def green(t):
    return c("32", t)


def red(t):
    return c("31", t)


def fmt_num(v):
    if v is None:
        return "–"
    if isinstance(v, float) and not v.is_integer():
        return f"{v:.1f}"
    return str(int(v))


def bar(value, scale=10):
    filled = max(0, min(scale, round(value)))
    return green("█" * filled) + dim("░" * (scale - filled))


def spark(values, lo=0.0, hi=10.0):
    chars = []
    for v in values:
        if v is None:
            chars.append(dim("·"))
        else:
            frac = (v - lo) / (hi - lo) if hi > lo else 0
            chars.append(SPARK[max(0, min(7, int(frac * 8)))])
    return "".join(chars)


def delta_str(now, before):
    if now is None or before is None:
        return dim("no prior data")
    diff = now - before
    arrow = green(f"▲ +{diff:.1f}") if diff >= 0.05 else red(f"▼ {diff:.1f}") if diff <= -0.05 else "≈ +0.0"
    return f"{arrow} vs prior week ({before:.1f})"


# ---------------------------------------------------------------- commands

def cmd_new(args, cfg, con):
    d = date.fromisoformat(args.date) if args.date else date.today()
    existing = jcore.get_entry(con, d) or {"metrics": {}, "sections": {}}
    overrides = {}
    for item in args.set or []:
        key, sep, val = item.partition("=")
        if not sep:
            sys.exit(f"error: --set expects key=value, got {item!r}")
        overrides[key.strip()] = val.strip()
    interactive = not overrides and args.notes is None

    if interactive:
        verb = "Editing" if existing["metrics"] else "New entry for"
        print(bold(f"{verb} {d:%A, %B %d, %Y}") + dim("  (Enter keeps [default] / skips)"))

    metrics = {}
    for m in cfg["metrics"]:
        key, prev = m["key"], existing["metrics"].get(m["key"])
        if key in overrides:
            try:
                metrics[key] = jcore.coerce_metric(m, overrides.pop(key))
            except ValueError as err:
                sys.exit(f"error: {err}")
        elif interactive:
            metrics[key] = ask_metric(m, prev)
        else:
            metrics[key] = prev
    if metrics.get("productivity") is None:
        sys.exit("error: a productivity rating (1-10) is required (--set productivity=N)")

    sections = {}
    for p in cfg["prompts"]:
        label, prev = p["label"], existing["sections"].get(p["label"], "")
        if p["key"] in overrides:
            sections[label] = overrides.pop(p["key"])
        elif interactive:
            hint = dim(f" [{prev}]") if prev else ""
            sections[label] = input(f"  {label}{hint}: ").strip() or prev
        else:
            sections[label] = prev

    for key in overrides:
        sys.exit(f"error: unknown --set key {key!r} (see config.json)")

    prev_notes = existing["sections"].get("Journal", "")
    if args.notes is not None:
        sections["Journal"] = args.notes
    elif interactive:
        print(f"  Journal (free-form, finish with an empty line){dim(' [Enter keeps previous]') if prev_notes else ''}:")
        lines = []
        while True:
            try:
                line = input("  > ")
            except EOFError:
                break
            if not line:
                break
            lines.append(line)
        sections["Journal"] = "\n".join(lines) or prev_notes
    else:
        sections["Journal"] = prev_notes

    jcore.upsert_entry(con, d, metrics, sections)

    entries = jcore.all_entries(con)
    current, longest = streaks(set(entries), d)
    wavg = week_avg(entries, week_start(d))
    print()
    print(f"Saved entry for {bold(d.isoformat())}  "
          f"productivity {bold(fmt_num(metrics['productivity']))}/10  {bar(metrics['productivity'])}")
    print(f"Streak: {bold(current)} day{'s' if current != 1 else ''} (best {longest})  ·  "
          f"week so far: {bold(f'{wavg:.1f}') if wavg else '–'} avg")

    if d == date.today() == week_start(d):  # it's Sunday: the previous week just closed
        print()
        print(dim("It's Sunday — here is your report for the week that just ended:"))
        print_week_report(entries, week_start(d) - timedelta(days=7), cfg)


def ask_metric(m, prev):
    lo, hi = m.get("min", 0), m.get("max", 10)
    required = m.get("required", False)
    while True:
        default = f" [{fmt_num(prev)}]" if prev is not None else ("" if required else " [skip]")
        raw = input(f"  {m['label']} ({lo}-{hi}){dim(default)}: ").strip()
        if not raw:
            if prev is not None:
                return prev
            if not required:
                return None
            print("    a value is required")
            continue
        try:
            return jcore.coerce_metric(m, raw)
        except ValueError as err:
            print(f"    {err}")


def cmd_auto_report(cfg, con):
    """Print the completed week's report once per week (first run on/after Sunday)."""
    entries = jcore.all_entries(con)
    if not entries:
        return
    completed_ws = week_start(date.today()) - timedelta(days=7)
    marker = MARKER_PATH.read_text().strip() if MARKER_PATH.exists() else ""
    if marker == completed_ws.isoformat():
        return
    print(dim("Your weekly productivity report is ready:"))
    print_week_report(entries, completed_ws, cfg)
    print()
    MARKER_PATH.write_text(completed_ws.isoformat() + "\n")


def cmd_report(args, cfg, con):
    if args.auto:
        cmd_auto_report(cfg, con)
        return
    entries = jcore.all_entries(con)
    if not entries:
        sys.exit("No entries yet — run journal.py to write your first one.")
    if args.date:
        ws = week_start(date.fromisoformat(args.date))
    elif args.last:
        ws = week_start(date.today()) - timedelta(days=7)
    else:
        ws = default_report_week(date.today())
    print_week_report(entries, ws, cfg)


def print_week_report(entries, ws, cfg):
    data = jcore.week_report_data(entries, ws, cfg, date.today())
    we = ws + timedelta(days=6)
    print()
    print(bold(f"━━━ Week of {ws:%b %d} – {we:%b %d, %Y} ━━━"))
    for day in data["days"]:
        d = date.fromisoformat(day["date"])
        p = day["productivity"]
        if p is None:
            marker = dim("—") if day["future"] else dim("not logged")
            print(f"  {d:%a %b %d}  {dim('·' * 10)}     {marker}")
        else:
            print(f"  {d:%a %b %d}  {bar(p)}  {fmt_num(p):>2}   {dim(day['win'][:44])}")

    if data["average"] is None:
        print(dim("  No entries this week."))
        return
    best, worst = date.fromisoformat(data["best"]), date.fromisoformat(data["worst"])
    week_average = data["average"]
    print()
    print(f"  Average: {bold(f'{week_average:.1f}')}/10   "
          f"{delta_str(week_average, data['prior_average'])}")
    print(f"  Best {best:%a} ({fmt_num(entries[best]['metrics']['productivity'])})  ·  "
          f"lowest {worst:%a} ({fmt_num(entries[worst]['metrics']['productivity'])})  ·  "
          f"{data['logged']}/7 days logged")

    if data["metrics"]:
        print()
        print(dim(f"  {'Metric':<28}{'avg':>6}{'prior':>8}"))
        for m in data["metrics"]:
            before = m["prior"]
            trend = "" if before is None else (
                green(" ▲") if m["avg"] > before else red(" ▼") if m["avg"] < before else " ≈")
            print(f"  {m['label']:<28}{m['avg']:>6.1f}{'' if before is None else f'{before:>8.1f}':>8}{trend}")


def cmd_stats(args, cfg, con):
    entries = jcore.all_entries(con)
    data = jcore.stats_data(entries, cfg, date.today())
    if data is None:
        sys.exit("No entries yet — run journal.py to write your first one.")
    first, last = date.fromisoformat(data["first"]), date.fromisoformat(data["last"])
    print()
    print(bold(f"━━━ All-time ({first:%b %d, %Y} → {last:%b %d, %Y} · {data['count']} entries) ━━━"))
    overall = data["overall"]
    streak = data["current_streak"]
    print(f"  Overall productivity: {bold(f'{overall:.1f}')}/10   "
          f"streak: {bold(streak)} day{'s' if streak != 1 else ''} "
          f"(longest {data['longest_streak']})")

    recent = data["weekly"][-12:]
    print()
    print(f"  Weekly trend: {spark([w['avg'] for w in recent])}")
    for w in recent[-8:]:
        ws = date.fromisoformat(w["week_start"])
        tag = dim(" (in progress)") if w["in_progress"] else ""
        if w["avg"] is None:
            print(f"    {ws:%b %d}  {dim('no entries')}{tag}")
        else:
            print(f"    {ws:%b %d}  {bar(w['avg'])}  {w['avg']:.1f}{tag}")

    if data["correlations"]:
        print()
        print(f"  What moves your productivity {dim('(correlation, needs 5+ days of data)')}:")
        for corr in data["correlations"]:
            print(f"    {corr['label']:<28}{corr['r']:+.2f}  {dim(corr['strength'])}")


def cmd_log(args, cfg, con):
    entries = jcore.all_entries(con)
    if not entries:
        sys.exit("No entries yet.")
    for d in sorted(entries)[-args.n:]:
        e = entries[d]
        p = e["metrics"].get("productivity")
        win = (e["sections"].get("Top wins today") or "").split("\n")[0][:50]
        print(f"  {d:%a %Y-%m-%d}  {fmt_num(p):>2}/10  {bar(p) if p is not None else ''}  {dim(win)}")


def cmd_show(args, cfg, con):
    d = date.fromisoformat(args.entry_date)
    e = jcore.get_entry(con, d)
    if e is None:
        sys.exit(f"No entry for {args.entry_date}.")
    print(bold(f"{d:%A, %B %d, %Y}"))
    for m in cfg["metrics"]:
        if m["key"] in e["metrics"]:
            print(f"  {m['label']}: {fmt_num(e['metrics'][m['key']])}")
    for heading, text in e["sections"].items():
        if text:
            print(f"\n{bold(heading)}\n{text}")


def cmd_serve(args, cfg, con):
    con.close()
    import server
    server.serve(args.port)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd")

    p_new = sub.add_parser("new", help="write or edit a daily entry")
    p_new.add_argument("--date", help="entry date YYYY-MM-DD (default: today)")
    p_new.add_argument("--set", action="append", metavar="KEY=VALUE",
                       help="set a metric or prompt non-interactively (repeatable)")
    p_new.add_argument("--notes", help="free-form journal text (non-interactive)")

    p_rep = sub.add_parser("report", help="weekly report")
    p_rep.add_argument("--date", help="any date inside the week to report on")
    p_rep.add_argument("--last", action="store_true", help="previous completed week")
    p_rep.add_argument("--auto", action="store_true",
                       help="shell-startup mode: print the completed week once per week, else stay silent")

    sub.add_parser("stats", help="all-time trends, streaks, correlations")

    p_log = sub.add_parser("log", help="list recent entries")
    p_log.add_argument("-n", type=int, default=14, help="how many entries (default 14)")

    p_show = sub.add_parser("show", help="print one entry")
    p_show.add_argument("entry_date", help="YYYY-MM-DD")

    p_srv = sub.add_parser("serve", help="run the web UI")
    p_srv.add_argument("--port", type=int, default=8765)

    commands = {"new": cmd_new, "report": cmd_report, "stats": cmd_stats,
                "log": cmd_log, "show": cmd_show, "serve": cmd_serve}
    argv = sys.argv[1:]
    if not argv or argv[0] not in set(commands) | {"-h", "--help"}:
        argv = ["new"] + argv
    args = parser.parse_args(argv)

    cfg = load_config()
    con = jcore.connect()
    try:
        commands[args.cmd](args, cfg, con)
    finally:
        con.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\naborted — nothing saved")
        sys.exit(130)
