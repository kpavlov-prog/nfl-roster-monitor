# NFL Roster Monitor

This repo checks NFL.com and ESPN roster pages on a schedule, saves latest team snapshots, and keeps permanent CSV logs by team.

## Files

- `.github/workflows/roster-check.yml` - scheduled GitHub Action, every 15 minutes
- `scripts/check_rosters.py` - roster fetcher/diff/log script
- `data/latest/` - latest snapshot per team
- `data/team-logs/` - permanent team-specific change logs
- `data/all-activity.csv` - all roster activity
- `index.html` - simple dashboard for GitHub Pages

## First run

Go to **Actions** -> **NFL Roster Check** -> **Run workflow**.

The first run creates a baseline. Later runs detect changes.

## GitHub Pages dashboard

Go to **Settings** -> **Pages** -> Source: `Deploy from a branch` -> Branch: `main` -> Folder: `/root`.
