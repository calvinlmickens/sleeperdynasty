# Dynasty Sleeper Migration v0.4

Standalone Sleeper league-state refresh package for **1 Genius and 9 Idiots**.

## Production flow

Sleeper API → GitHub Actions → reconciliation → Google Drive `latest/` baseline.

The workflow downloads the prior validated baseline before every refresh. It then pulls fresh Sleeper state, compares ownership against the prior snapshot, reconciles roster changes against completed Sleeper transactions, and persists the new baseline only when `manifest.json` reports `overall_status: PASS`.

A failed refresh never overwrites the last known-good Drive state.

## Persistent Drive files

- `manifest.json`
- `league_state_current.csv`
- `roster_summary_current.csv`
- `league_transactions_master.csv`
- `reconciliation_report.csv`
- `roster_delta_reconciliation.csv`

Raw API responses and other diagnostic outputs remain available through the GitHub Actions artifact rather than being permanently duplicated in Drive.

## Run 1

The first Drive-backed run should report `baseline_status: ESTABLISHED_THIS_RUN` and create the six persistent files in the app-owned `latest/` folder.

## Run 2

The second run should report `baseline_status: COMPARED_TO_PREVIOUS`. Any ownership delta must reconcile to a completed Sleeper transaction or the refresh fails with `UNRECONCILED_ROSTER_DELTA` and Drive is not overwritten.

## v0.5 — Weekly matchup derivation + enrichment interface

v0.5 adds a compact framework-input layer derived from the validated Sleeper baseline:

- `weekly_matchup_context.csv` — 54-row target/opponent player view with starter slots and optional enrichment
- `weekly_matchup_summary.csv` — matchup identity, starter/bench counts, scores, and projection aggregates when available
- `framework_matchup_packet.md` — human-readable platform-state packet with an explicit framework readiness gate
- `player_week_context_TEMPLATE.csv` — canonical interface for external projections, practice/injury, game environment, role and ecosystem enrichment

The pipeline does not fabricate missing external context. Without a populated enrichment file the packet is generated, but the gate remains `HOLD_EXTERNAL_INTELLIGENCE_REQUIRED`; CPI/WUS/FLEX/final lineup execution must not proceed.
