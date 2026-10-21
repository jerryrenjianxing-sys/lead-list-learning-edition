# Development workflow

Use the public GitHub history and an ordinary development branch. `origin` is the product repository; `upstream` is MediaCrawler. Keep baseline branches and user changes. Candidate builds do not create release tags or publish assets.

## Contracts before implementation

Read GLOSSARY.md and the version specification. Caller-visible seams are the installed Skill/CLI, authenticated HTTP, diagnostic lifecycle, and Electron/installer lifecycle. Tests describe observable results through these seams. Use real temporary SQLite and isolated paths; inject only filesystem/process/clock/network adapters where failure must be controlled.

For behavior changes, record a failing test, implement the smallest working slice, and verify it before adding the next slice. Structural refactoring follows a passing contract and review. Do not test private methods, duplicate implementation formulas, or create interfaces for speculative callers. Existing installation/security tests cover different risks and stay in the suite.

## Module responsibilities

AgentAccess owns bootstrap, capability projection and authorized readiness. The client owns discovery, local-only transport, compatibility, receipts and recovery. SelfTests owns isolated diagnostics and deadlines. WorkspaceReadModel owns read projections. HTTP handlers adapt requests and compose these modules. Queue, sessions, analysis and database retain their existing state/transaction guarantees.

Use explicit types for changed request/result shapes. Keep credential values out of exceptions, copied Skill text, fixtures captured from real users, and test reports. Treat upstream content as upstream: avoid bulk formatting or unrelated rewrites. Preserve API v1. The approved 0.5.0 specification migrates schema 4 to 5 after backup; old applications must receive their schema 4 backup, never the new database.

## Review and delivery

Review changes on two independent axes: Standards (this file, glossary and applicable ADRs) and Spec (the approved version specification). The 0.5.0 change uses 2494a46a42c9d5492cbaa9034d36e70d7fe7937e as its fixed baseline and docs/specs/0.5.0-state-recovery.md as its spec. Report hard failures and heuristic design concerns separately. Run targeted checks during each slice, then full regressions, packaged runtime checks and isolated upgrades. The rollback baseline remains 394bc39211221a3ee645e75d1eae9110331751f8 (0.4.3).

Version comes only from pyproject.toml. Homepage bootstrap, installed Skill and ZIP share the same source; generated per-machine paths are not packaged credentials. Keep dependency locks unchanged except the project version metadata. The user approved a public 0.5.0 preview and idle local upgrade after acceptance; retain all old releases, especially 0.4.3 and its hashes. Follow the pinned release policy and document pending receiving-Agent, real-platform, long-duration and clean-system checks without claiming they passed. Historical candidate-only records remain intact.
