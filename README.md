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
