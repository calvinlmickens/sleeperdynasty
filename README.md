# Dynasty Sleeper Migration — v0.1

A lightweight standalone Python package for migrating the `1 Genius and 9 Idiots` league-state input layer from ESPN to Sleeper.

## What v0.1 does

- Pulls Sleeper league, users, rosters, matchups, transactions, traded picks, and player metadata.
- Normalizes current roster ownership to stable Sleeper IDs.
- Generates a persistent transaction master keyed by Sleeper transaction ID.
- Runs hard reconciliation checks before downstream framework use.
- Supports fixture/offline mode so the logic can be tested without internet access.

## What v0.1 intentionally does NOT do yet

- Projections / league-adjusted projections
- External injury/practice/role/news enrichment
- Waiver-state inference
- Opponent Registry material-delta logic
- CPI, WUS, FLEX, or lineup decisions

Those are later build phases so platform ingestion remains separate from Dynasty Framework doctrine.

## Install

```bash
pip install -e .
```

## Live run

```bash
dynasty-refresh --week 1 --output-dir output
```

## Fixture run

```bash
dynasty-refresh --week 1 --fixture-dir tests/fixtures --output-dir output
```

## Production outputs in v0.1

- `league_state_current.csv`
- `league_transactions_current.csv`
- `league_transactions_master.csv`
- `traded_picks_current.csv`
- `reconciliation_report.csv`

The command exits with status 2 if a critical reconciliation check fails.

## Sleeper connectivity health check

Before any live refresh, verify that the **package runtime itself** can reach Sleeper and resolve the configured league:

```bash
dynasty-refresh --healthcheck --week 1
```

A PASS verifies the league ID, users, 10 rosters, Week matchup endpoint, and Sleeper player metadata endpoint. A FAIL does **not** silently substitute stale league state.

For a lighter connectivity-only test that skips the large player dictionary:

```bash
dynasty-refresh --healthcheck --week 1 --skip-players-healthcheck
```
