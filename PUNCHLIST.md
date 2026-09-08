# Dynasty Sleeper Migration Punchlist

## Governance
- [x] Migration is platform-input replacement only; no Dynasty Framework doctrine changes.
- [x] Sleeper IDs are canonical live-state keys.
- [x] Stale ESPN files will cease to be current truth once Sleeper validation is complete.

## Phase 1 — Live validation
- [x] Standalone package skeleton.
- [x] Sleeper connectivity health check.
- [x] GitHub Actions runtime reaches Sleeper.
- [x] Correct league ID/name resolves.
- [x] 10 users and 10 rosters resolve.
- [x] Matchup endpoint resolves.
- [x] Player dictionary endpoint resolves.
- [x] Full live refresh code built.
- [x] Raw source bundle built.
- [x] `manifest.json` freshness/count/hash layer built.
- [x] GitHub artifact upload workflow built.
- [ ] Run first live full refresh in GitHub.
- [ ] Inspect `roster_summary_current.csv` and confirm RuffRyders Reloaded roster ID.
- [ ] Validate actual roster counts/ownership and accept first known-good baseline.

## Phase 2 — Roster/transaction drift protection
- [x] Stable transaction normalization by Sleeper transaction ID.
- [x] Season-to-date transaction pull through requested week.
- [x] Snapshot ownership-delta function.
- [x] Roster-delta vs transaction reconciliation function.
- [x] `UNRECONCILED_ROSTER_DELTA` hard-stop behavior.
- [ ] Persist prior validated snapshot between production runs (Google Drive phase).
- [ ] Validate one real add/drop or waiver change against delta logic.
- [ ] Validate one trade when available.
- [ ] Add waiver-priority drift monitoring.

## Next
- [ ] Connect Google Drive handoff after GitHub artifact bundle passes.
- [ ] Add compact waiver engine.
- [ ] Add projection/external-intelligence adapter.
- [ ] Connect Opponent Registry material-change triggers.
- [ ] Generate `framework_matchup_packet.md`.
- [ ] Parallel ESPN/Sleeper validation and ESPN retirement.
