# Tirek

<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
Delegated by the user: React + TypeScript + Vite frontend, Python + FastAPI + SQLite backend. Python provides a direct integration boundary for the user's future ML implementation.

## Users and purpose
Procurement managers reviewing replenishment recommendations by product and warehouse. The workflow is documented in docs/product-spec.md and docs/platform-spec.md: import, inspect quality, calculate, investigate, override with a reason, approve an immutable snapshot, export CSV.

## Constraints and evidence
Partner workbooks are not committed. The built-in adapter connects compatible uploaded workbooks to forecast v2 and procurement decisions; explicit stockout intervals and pseudonymous client events activate the regular-demand model. Built-in demo and generated sample workbooks remain explicitly synthetic; never imply that the canned demo ran ML. Currency KZT; Russian interface; null differs from zero. The single-server MVP has registration, cookie authentication and isolated personal workspaces. See README.md and docs/mvp-verification.md for checked workflows and limits.

## Principles
Make the reason behind a decision inspectable. Expose missing inputs. Keep data provenance visible. Preserve manual decisions and approved snapshots. Avoid adding marketing claims.

## Open decisions
Shared team roles/invitations, email verification/recovery, a durable multi-worker queue, ERP integration and operational validation on complete partner data remain future work. External AI is optional, read-only, server configured, and receives calculation facts only with explicit UI consent.
