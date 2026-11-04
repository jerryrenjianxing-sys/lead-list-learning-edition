# 0.5.4 desktop reconnect and analysis-list validation

Behavior revision: `af6346d9a9c57112bd1e2d6229caa91d07c16e5e`.
Comparison baseline: `ae38cf0c6932e03aaaf53869c29c7473339d1f48`.
Specification: [desktop reconnect](specs/0.5.4-desktop-reconnect.md).

## Reproductions and observed behavior

| Check | Before | After |
| --- | --- | --- |
| Authorized discovery on the existing large workspace | TimeoutError after 8.063 s; full diagnostics took 12.526 s | Lightweight authenticated handshake completed in 0.037 s, with the same workspace identity |
| Isolated packaged desktop after a transient readiness failure | Remained on its error page after the backend recovered | Restored the workspace automatically; healthy polling and manually reopened settings retained their routes |
| Complete analysis list, 50 analyses and roughly 1.9 million inputs | Authenticated HTTP took 21.328 s, exceeding the existing 15 s page deadline | Installed repaired HTTP took 4.735 s; the complete JSON matched the original response |
| Analysis input aggregation | Primary-key scan took 19.875 s | Sequential grouped scan took 4.219 s with identical counts |
| Concurrent completion while listing analyses | Mixed an older analysis state with newer coverage | Analysis rows and coverage came from one read snapshot |

The timing values are observations from this workspace, not latency guarantees for every data size or machine. The original failing outputs and local deployment receipts are retained outside the public repository. Credentials, browser profiles, business records, machine paths and private instance identifiers are not included in this report.

## Automated checks

- Full Python suite: **809 passed, 2 skipped** using `.venv/Scripts/python.exe -m pytest tests -q`.
- Targeted Python contracts: **42 passed**, covering the analysis list, analysis writes/exports, Agent access, Electron packaging and desktop ownership.
- Electron suite: **41 passed** using `npm.cmd --prefix electron run test`.
- Real isolated packaged desktop: `electron/tests/reconnect.cjs` reproduced a failed readiness response, then verified automatic recovery, healthy-route preservation, manually reopened-route preservation and complete process cleanup.
- Candidate bundled-runtime and installed native self-tests: all local steps passed, with temporary-workspace cleanup confirmed. These checks did not run social-platform collection.
- Installed application: the original workspace opened; the overview displayed the complete analysis count without a timeout and retained its route beyond a desktop health-check interval. Normal desktop exit and independent restart were also verified.

Tests use authenticated HTTP, real temporary SQLite and isolated desktop data paths. The concurrency test first failed against the pristine pre-change read projection and then passed after the snapshot repair. Empty analyses, nullable result schemas, metadata, ordering and all four coverage states are compared with detail responses.

### Earlier full-suite failures

The first full run had 808 passes, 2 skips and one failure in the unchanged 1.8-second diagnostic timeout/cleanup test. Its local phase timed out and process reaping was not confirmed, so cleanup was correctly not claimed successful. The changed analysis read path had not been reached.

The second full run had 808 passes, 2 skips and a different failure: a synthetic retry probe returned `startup_failed`, and the fixture did not observe its next attempt within its existing deadline. That test passed in the first run; the diagnostic test passed in the second run. Both tests passed separately at their original limits.

A competing workload was then measured: the live application still using the old analysis projection repeatedly requested a 17–21-second list every 10 seconds. After normally stopping that application, both failing tests passed together, and the complete unmodified suite passed with 809 passes and 2 skips. No assertion, timeout, test order or skip marker was changed. The earlier failures are retained; this does not establish a unique cause or claim a diagnostic/probe lifecycle fix.

## Independent review

### Standards

0 actionable documented-standard findings and 0 actionable heuristic findings. The coverage module owns aggregation; the workspace read model owns JSON projection and the read snapshot. Shared helpers avoid duplicate formulas and speculative interfaces.

### Spec

0 actionable findings. The repair preserves authenticated identity checks, stale-result fencing, startup ownership and safe exit. List responses preserve their existing fields and counts without a migration, index, dependency or collection change.

## Local installation and delivery scope

The installed 0.5.4 application was normally stopped before each replacement. Complete current data and browser-profile backups, database hashes and backup inventories were verified. Original desktop and backend files, the official manifest and repair checksums were retained separately. Business table counts, jobs, retry plans, capability counts, sessions and artifacts matched before and after installation.

This change preserves schema 6, API v1, the browser implementation, paused jobs, the 1/2/4-hour capability rules, the 999 threshold and task retry intervals. It does not restart capability cycles. The local installation is a repair of 0.5.4; this source delivery does not replace the existing v0.5.4 tag or release assets.
