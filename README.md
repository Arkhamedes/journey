# Productivity Journal

A zero-dependency daily journal for staying relentlessly productive. Journal
every day, rate the day out of 10, and get a weekly report — ready Sunday
morning — with averages, trends, streaks, and what actually moves your
productivity. Use it from the terminal or the local web app — both share the
same SQLite database (`journal.db`), so they always agree.

## Web app

```bash
./journal.py serve      # then open http://127.0.0.1:8765
```

Or run it permanently as a systemd user service so the site is always up
(the unit assumes the repo is cloned to `~/journey`; edit `ExecStart` otherwise):

```bash
cp journey.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now journey.service
loginctl enable-linger $USER    # keep it running after logout / at WSL boot
```

Managing the service:

```bash
systemctl --user status journey            # is it running?
systemctl --user restart journey           # apply code/config changes
systemctl --user disable --now journey     # stop it and stop auto-starting
loginctl disable-linger $USER              # let WSL shut down when idle (service then runs only while WSL is up)
```

To change the port, edit the `ExecStart` line in
`~/.config/systemd/user/journey.service` (add `--port 9000`), then
`systemctl --user daemon-reload && systemctl --user restart journey`.

Three views: **Today** (the entry form — tap a 1-10 rating, fill the prompts,
save), **Week** (daily bar chart, weekly average with delta vs prior week,
best day, per-metric comparison), and **Trends** (weekly average line chart,
streaks, correlations, recent entries). Light and dark mode follow your
system. The server binds to 127.0.0.1 only — your journal is never exposed to
the network.

## Daily use (CLI)

```bash
./journal.py            # write (or edit) today's entry interactively
```

You'll be asked for:

| Metric | Scale |
|---|---|
| **Productivity** (required) | 1–10 |
| Energy | 1–10 |
| Deep-work hours | 0–24 |
| Sleep last night | hours |
| Exercise | minutes |
| Pages read today | 0–100 |
| LeetCode problems solved | 0–100 |

…plus three reflection prompts (*top wins*, *what slowed you down*,
*tomorrow's #1 priority*) and free-form journal text. Everything except the
productivity rating is optional — press Enter to skip.

After saving you immediately see your current streak and the week-so-far
average, so slipping is visible the same day.

## The Sunday report

Weeks run **Sunday → Saturday**. On Sunday morning, `./journal.py report`
shows the week that just closed; on any other day it shows the current
week-to-date. Journaling on a Sunday also prints the completed week's report
automatically.

```bash
./journal.py report          # this week (on Sundays: the completed week)
./journal.py report --last   # force the previous completed week
./journal.py stats           # all-time trends, streaks, correlations
./journal.py log             # recent entries at a glance
./journal.py show 2026-07-01 # print one entry
```

The weekly report includes: a bar per day, weekly average with ▲/▼ delta vs
the prior week, best/lowest day, days-logged count, and per-metric averages
vs last week. `stats` adds a multi-week trend sparkline, longest streak, and
Pearson correlations (e.g. how strongly sleep predicts your productivity —
needs 5+ days of data per metric).

## Tips

Add an alias so you can journal from anywhere:

```bash
echo 'alias journal="$HOME/github/productivity-journal/journal.py"' >> ~/.bashrc
```

Automate the Sunday report by adding this to `~/.bashrc` — the completed
week's report prints in the first terminal you open on/after each Sunday,
then stays silent for the rest of the week:

```bash
"$HOME/github/productivity-journal/journal.py" report --auto
```

Backups are automatic: every save (CLI or web) commits `journal.db` and
pushes to the remote in the background. If you're offline the save still
succeeds and the next save pushes everything. Set `"git_sync": false` in
`config.json` to turn this off.

## Data

Entries live in `journal.db` (SQLite) — one row per day with metrics and
journal text as JSON. Legacy markdown files under `entries/` are imported
automatically the first time they're seen and never overwritten; the DB is
the source of truth after that.

## Everything this project touches (for uninstalling/adjusting)

- `~/.config/systemd/user/journey.service` — the always-on web server
  (disable with the commands above)
- `~/.bashrc` — two additions: the `journal` alias and the Sunday
  `report --auto` line (delete either line to remove)
- `.last-auto-report` (in this folder) — remembers which weekly report was
  already shown; safe to delete
- Auto git backup — `"git_sync": false` in `config.json` turns it off

## Customizing metrics

Edit `config.json` — each metric has a `key`, `label`, `min`/`max`, optional
`float` and `required`. Anything you add is prompted for daily, averaged in
weekly reports, and correlated against productivity in `stats`. Ideas worth
tracking: mood, distraction count, caffeine, screen time before bed, hours in
meetings.

For scripting or quick capture without prompts:

```bash
./journal.py new --set productivity=8 --set focus_hours=5.5 --set wins="shipped POS demo" --notes "Long day but good."
```
