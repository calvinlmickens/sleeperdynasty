# Sleeper Migration Punchlist

## Phase 0 — Governance lock
- [x] Treat Sleeper migration as platform-input replacement only.
- [x] Keep Dynasty Framework doctrine authoritative.
- [x] Use Sleeper league ID `1402840136382742528` as canonical league identity.
- [x] Use stable Sleeper `roster_id`, `owner_id`, and `player_id` keys.
- [x] Preserve persistent LIARD / Opponent Registry; refresh only material opponent-state changes.

## Phase 1 — Core live league-state ingest
- [x] Create standalone Python package skeleton.
- [x] Add Sleeper API client with fixture/offline mode.
- [x] Pull league metadata/settings.
- [x] Pull league users.
- [x] Pull current rosters and starters.
- [x] Pull weekly matchup rows.
- [x] Pull weekly transactions.
- [x] Pull traded-pick state.
- [x] Cache/normalize Sleeper player IDs and metadata.
- [x] Generate `league_state_current.csv`.
- [x] Generate `league_transactions_current.csv`.
- [x] Generate/merge `league_transactions_master.csv`.
- [x] Generate `traded_picks_current.csv`.
- [x] Generate hard reconciliation report.
- [ ] Run against the live league and capture first real output.
- [ ] Identify CMac/RuffRyders Reloaded `roster_id` and persist it.
- [ ] Validate all 10 teams and 270 owned players against current Sleeper UI.

## Phase 2 — State reconciliation and drift prevention
- [x] Check league ID and team count.
- [x] Check unique player ownership.
- [x] Check player-ID resolution.
- [x] Check expected roster size.
- [x] Check starter membership.
- [x] Check matchup roster coverage and pairings.
- [ ] Compare each new snapshot with prior snapshot.
- [ ] Reconcile every ownership delta against a Sleeper transaction ID.
- [ ] Flag `UNRECONCILED_ROSTER_DELTA`.
- [ ] Flag completed transaction/ownership mismatch.
- [ ] Add waiver-state reconciliation.
- [ ] Block CPI/WUS/FLEX execution on critical reconciliation failure.

## Phase 3 — Weekly framework inputs
- [ ] Derive `weekly_matchup_intake` as an in-memory/view output.
- [ ] Derive `weekly_matchup_summary` rather than persist it.
- [ ] Add CMac current-opponent resolution.
- [ ] Detect empty starter slots and bench activation paths.
- [ ] Generate compact framework-ready matchup state.

## Phase 4 — External enrichment adapter
- [ ] Define projection provider interface.
- [ ] Store provider/base projection separately from league-adjusted interpretation.
- [ ] Add current injury/practice status enrichment.
- [ ] Add role/depth-chart/usage changes.
- [ ] Add Vegas game/team totals.
- [ ] Add weather where relevant.
- [ ] Add offensive ecosystem flags.
- [ ] Timestamp every external source so stale enrichment cannot silently persist.

## Phase 5 — Waiver engine
- [ ] Compute unrostered player universe from Sleeper ownership.
- [ ] Add current waiver/free-agent state logic.
- [ ] Incorporate trending adds/drops as supplemental signal.
- [ ] Filter to dynasty-relevant candidates only.
- [ ] Generate compact `waiver_watch.csv` rather than 498-row theater.
- [ ] Retire `waiver_watch_summary_v2` as a persistent artifact.

## Phase 6 — Opponent Registry integration
- [ ] Resolve every Opponent Registry team to stable Sleeper `roster_id`/`owner_id`.
- [ ] Compare current roster/lineup structure against registry baseline.
- [ ] Trigger material delta on trade, major injury, meaningful add/drop, lineup-behavior shift, empty-slot change, or pressure-source change.
- [ ] Generate `opponent_registry_delta.csv` only when useful.
- [ ] Merge live pressure-source logic into the delta process.
- [ ] Preserve archetype/CPI doctrine unless evidence meets existing governance thresholds.

## Phase 7 — Framework presentation layer
- [ ] Generate `framework_matchup_packet.md` from normalized state + enrichment + registry delta.
- [ ] Include roster/matchup reconciliation status at top.
- [ ] Include transaction intelligence as a generated section.
- [ ] Keep CPI/WUS/FLEX decisions out of the ingestion package.

## Phase 8 — Migration cutover
- [ ] Run ESPN and Sleeper state in parallel for two validation cycles.
- [ ] Reconcile ownership, matchup identity, starters, transactions, and team mapping.
- [ ] Mark Sleeper pipeline authoritative after validation passes.
- [ ] Archive ESPN files as historical-only sources.
- [ ] Remove stale ESPN outputs from normal weekly execution path.

## Phase 9 — Production automation
- [ ] Make `dynasty-refresh --week N` one-command production entry point.
- [ ] Confirm the assistant execution environment can access Sleeper at runtime.
- [ ] Decide on on-demand assistant execution vs scheduled hosted execution.
- [ ] If scheduling through ChatGPT, test one scheduled refresh before relying on it operationally.
- [ ] Notify only on material state change or reconciliation failure unless a full report is requested.
