# Dynasty Sleeper Migration Punchlist — v0.4

## Completed
- [x] Governance lock: platform-input migration only
- [x] Sleeper live API health check
- [x] Live league identity validation
- [x] 10-user / 10-roster validation
- [x] 270-player ownership baseline validation
- [x] Stable RuffRyders Reloaded roster ID resolution
- [x] Live matchup pull
- [x] Transaction normalization
- [x] First known-good Sleeper baseline
- [x] Google OAuth authentication
- [x] App-owned My Drive folder creation
- [x] Configured Drive folder ID read/write test
- [x] Google Drive persistence helper
- [x] Last-known-good overwrite protection
- [x] Prior baseline download before refresh
- [x] Roster-delta vs transaction reconciliation hook

## Immediate validation
- [ ] Run 1: establish Drive-backed `latest/` baseline
- [ ] Confirm six persistent files appear in Drive
- [ ] Confirm manifest `overall_status: PASS`
- [ ] Confirm manifest `baseline_status: ESTABLISHED_THIS_RUN`
- [ ] Run 2: download and compare prior Drive baseline
- [ ] Confirm `baseline_status: COMPARED_TO_PREVIOUS`
- [ ] Confirm no unexplained ownership drift

## Next
- [ ] Registry-impact classification for material opponent changes
- [ ] Compact waiver engine
- [ ] Projection/external-intelligence adapter
- [ ] Framework matchup packet generation
- [ ] Schedule production refresh cadence
- [ ] Retire ESPN current-state truth after final migration validation

## Phase 3 — Framework input derivation
- [x] Derive weekly matchup identity from validated Sleeper state
- [x] Derive starter/bench/slot context for target and opponent
- [x] Add canonical external enrichment adapter schema
- [x] Add framework readiness gate
- [x] Generate structural framework matchup packet
- [ ] Select/live-test external projection source
- [ ] Attach fresh injury/practice/role/ecosystem/Vegas/weather enrichment
- [ ] Open framework gate only after required enrichment coverage passes
