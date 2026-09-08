# Dynasty Sleeper Migration — v0.3

A lightweight standalone Python package for migrating the **1 Genius and 9 Idiots** league-state input layer from ESPN to Sleeper.

## v0.3 milestone

v0.3 adds the first production-style live bundle:

- full live league/user/roster/matchup pull
- Sleeper player dictionary cache inside the run bundle
- season-to-date transaction rounds through the requested week
- normalized current league state
- roster summary for stable roster/team ID resolution
- traded-pick snapshot
- hard reconciliation report
- optional previous-state roster-delta reconciliation
- `manifest.json` with freshness timestamp, counts, hashes, target-roster resolution, and overall PASS/FAIL
- GitHub Actions workflow that uploads the entire output directory as a downloadable artifact

## Health check

```bash
dynasty-refresh --healthcheck --week 1
```

or without package installation:

```bash
python -m dynasty_sleeper.cli --healthcheck --week 1
```

## Live refresh

```bash
python -m dynasty_sleeper.cli --week 1 --output-dir output
```

## Compare against a prior snapshot

```bash
python -m dynasty_sleeper.cli --week 1 --output-dir output --previous-state state/league_state_previous.csv
```

## v0.3 outputs

- `league_state_current.csv`
- `roster_summary_current.csv`
- `league_transactions_current.csv`
- `league_transactions_master.csv`
- `traded_picks_current.csv`
- `reconciliation_report.csv`
- `roster_delta_reconciliation.csv`
- `manifest.json`
- `raw/*.json`

The run exits with status 2 if a critical state-reconciliation check fails or an observed ownership delta cannot be reconciled to Sleeper transactions.

## GitHub Actions

The included `.github/workflows/dynasty-sleeper-refresh.yml` supports manual execution with a Week input and uploads the generated `output/` directory as a GitHub artifact. Google Drive handoff is intentionally deferred until the live bundle passes validation.
