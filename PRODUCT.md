# Tirek

<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
Delegated by the user: React + TypeScript + Vite frontend, Python + FastAPI + SQLite backend. Python provides a direct integration boundary for the user's future ML implementation.

## Users and purpose
Procurement managers reviewing replenishment recommendations by product and warehouse. The workflow is documented in docs/product-spec.md and docs/platform-spec.md: import, inspect quality, calculate, investigate, override with a reason, approve an immutable snapshot, export CSV.

## Constraints and evidence
Partner workbooks are not committed. The built-in backend/app/ml_adapter.py connects six compatible SE workbooks to forecast v2 and the procurement decision core. Its real-data scope is SE, Almaty, pieces; stockout and customer-outlier forecasting remain separate modules. Platform demo data must remain explicitly synthetic; never imply that a demo request ran the model or LLM. Preserve contracts/openapi.yaml. Currency KZT; Russian interface; null differs from zero. Local single-user workspace, not a production authenticated SaaS. See README.md and docs/submission-analysis.md for review instructions and current limitations.

## Principles
Make the reason behind a decision inspectable. Expose missing inputs. Keep data provenance visible. Preserve manual decisions and approved snapshots. Avoid adding marketing claims.

## Open decisions
Production deployment, authentication, broader workbook support, stockout/customer integration and validation of supplier constraints belong to subsequent work.
