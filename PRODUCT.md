# Tirek

<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
Delegated by the user: React + TypeScript + Vite frontend, Python + FastAPI + SQLite backend. Python provides a direct integration boundary for the user's future ML implementation.

## Users and purpose
Procurement managers reviewing replenishment recommendations by product and warehouse. The workflow is documented in docs/product-spec.md and docs/platform-spec.md: import, inspect quality, calculate, investigate, override with a reason, approve an immutable snapshot, export CSV.

## Constraints and evidence
The user explicitly requested building the platform now and handling ML integration separately. Partner workbooks are not committed. The separate model/ directory contains a trained CatBoost artifact and evaluation, but it is not connected to the platform API. Platform demo data must remain explicitly synthetic; never imply that a demo request ran the model or LLM. Preserve contracts/openapi.yaml. Currency KZT; Russian interface; null differs from zero. Local single-user workspace, not a production authenticated SaaS.

## Principles
Make the reason behind a decision inspectable. Expose missing inputs. Keep data provenance visible. Preserve manual decisions and approved snapshots. Avoid adding marketing claims.

## Open decisions
Production deployment, authentication, model integration and real workbook normalization belong to subsequent work.
