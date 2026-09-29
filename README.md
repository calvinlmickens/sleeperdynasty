# Dynasty Sleeper Automation v0.6

Purpose: replace the ESPN league-state ingestion layer for **1 Genius and 9 Idiots** without changing Dynasty Framework doctrine.

## Production flow

Sleeper API -> GitHub Actions -> reconciliation -> Google Drive `latest/` -> Dynasty Framework.

## v0.6 enrichment layer

v0.6 automatically attempts to retrieve Sleeper weekly projections from Sleeper's separate projections host and builds `player_week_context.csv` every refresh.

The adapter:
- keeps Sleeper player IDs as the canonical key;
- uses current Sleeper injury/practice player-state fields;
- calculates a directional **league-adjusted projection** from projected stat components and the league's actual `scoring_settings` when matching stat keys are available;
- falls back to a Sleeper baseline fantasy projection when projected stat components cannot support a custom-score calculation;
- records projection method, contributing scoring keys, source timestamp, and a warning field;
- degrades safely if the projection endpoint is unavailable because the projection endpoint is not part of Sleeper's documented core league API.

## Framework gate

Automated projections do **not** by themselves open CPI/WUS/FLEX execution.

Possible gates:
- `HOLD_EXTERNAL_INTELLIGENCE_REQUIRED` - usable starter projections are not available.
- `HOLD_LIVE_INTELLIGENCE_SWEEP_REQUIRED` - projections are available, but current role/environment intelligence still needs validation.
- `OPEN_FOR_CPI_WUS_FLEX` - projections plus role/environment context are present.

This preserves Volume 3 governance: league state and projections may be automated, while current injury developments, role changes, offensive ecosystem changes, Vegas/weather context, and other decision-critical football intelligence must still be validated before final framework execution.

## Primary persistent outputs

- `manifest.json`
- `league_state_current.csv`
- `roster_summary_current.csv`
- `league_transactions_master.csv`
- `reconciliation_report.csv`
- `roster_delta_reconciliation.csv`
- `player_week_context.csv`
- `waiver_watch_v2.csv` — current Sleeper player universe minus current league roster ownership, ranked for RuffRyders Reloaded.
- `waiver_watch_summary_v2.csv` — candidate counts by priority add, speculative stash, streamer, and realistic drop candidates.
- `weekly_matchup_context.csv`
- `weekly_matchup_summary.csv`
- `framework_matchup_packet.md`

## Run

GitHub Actions: **Dynasty Sleeper Refresh**.

Manual fixture run:

```bash
python -m dynasty_sleeper.cli --week 1 --fixture-dir tests/fixtures --output-dir output
```

Every normal refresh (scheduled or manually dispatched) builds both waiver-watch
CSVs from the selected Sleeper league's current rosters and `/players/nfl`
universe. The current Sleeper weekly projection endpoint is joined when available;
unavailable projections remain blank and are flagged. Both files are uploaded to
the Google Drive `latest/` folder with the normal refresh outputs. The manifest
records the league/week, pool and output status, generation time, candidate and
projection counts, and any validation errors. Week 3 ESPN waiver files are not
inputs to this pipeline.
