# Smart-System-Flow
   DevOnboard AI

**Smart Developer Onboarding Assistant — Production-Ready MVP (v2.0)**

DevOnboard AI turns any software repository into an interactive, evidence-backed map of how the codebase actually works — so a new developer can explore its architecture, trace a real feature end to end, ask grounded questions, validate their local environment, and receive a first contribution task, all backed by facts pulled from the repository itself, never invented by a model.

> Understand any codebase. See how it works. Find where to contribute — safely, verifiably, and without ever sending a secret to a model.

---

## Table of Contents

- [Why DevOnboard AI](#why-devonboard-ai)
- [Signature Feature: Live Codebase Map & Feature Trace](#signature-feature-live-codebase-map--feature-trace)
- [System Architecture](#system-architecture)
- [Tech Stack](#tech-stack)
- [Getting Started](#getting-started)
- [API Overview](#api-overview)
- [AI / RAG Grounding & Guardrails](#ai--rag-grounding--guardrails)
- [Security & Privacy](#security--privacy)
- [Testing](#testing)
- [Observability](#observability)
- [Project Structure](#project-structure)
- [Roadmap](#roadmap)
- [Acceptance Criteria](#acceptance-criteria)
- [License](#license)

---

## Why DevOnboard AI

Onboarding to an unfamiliar codebase is slow and unreliable — docs go stale, tribal knowledge lives in people's heads, and generic AI chatbots hallucinate architecture that was never there. DevOnboard AI is **not** a chatbot skin over documentation. It's an interactive workspace where:

- The architecture is reconstructed from **real static analysis** — never hardcoded or guessed.
- Every node in the map opens the **actual source file** that justifies it.
- Every AI answer either **cites its evidence** (file, line, symbol) or explicitly says the repository doesn't contain enough information to answer.
- Setup guides and starter tasks are generated from the **repository's real configuration**, not templates.

**Design principle:** static analysis produces facts; the model explains and connects them.

## Signature Feature: Live Codebase Map & Feature Trace

The Live Codebase Map renders the repository as a living system, not a static diagram. Selecting a node reveals its purpose, files, symbols, dependencies, and dependents.

The Feature Trace follows a real question — e.g. *"How does login work?"* — through the actual code path:

```
Login UI → /login route → AuthService → UserRepository → Database → Response
```

Each step displays the real source file and a short grounded explanation. If confidence drops below threshold at any step, the trace halts and says so rather than guessing forward. Users can pause, step forward/backward, restart, or open the source directly in an embedded Monaco editor.

## System Architecture

```
Client → API Gateway (auth + rate limit) → Orchestrator → Analysis Sandbox
       → Evidence Graph → PostgreSQL/pgvector → AI/RAG (redaction filter → provider)
       → Orchestrator → SSE stream → Client
```

| Layer | Technology | Responsibility |
|---|---|---|
| Frontend | Next.js + TypeScript | Dashboard, routing, graph/code UI |
| Graph UI | React Flow | Interactive architecture rendering |
| Code viewer | Monaco Editor | Read-only, sandboxed source inspection |
| API / Gateway | FastAPI + Python | Orchestration, auth, rate limiting |
| Parsing | Tree-sitter + AST | Symbol / import / call extraction |
| Redaction | Custom secret-scanner | Strips credentials before any AI call |
| Storage | PostgreSQL + pgvector | Metadata + embeddings, row-level tenant security |
| Jobs | Redis + worker pool | Async analysis, idempotent, dead-letter queue |
| AI | OpenAI-compatible provider | Grounded explanations, circuit-breaker protected |
| Observability | OpenTelemetry + structured logs | Tracing, metrics, alerting |

Every hop emits a trace span tagged with `repository_id` and `request_id` for end-to-end debugging.

## Tech Stack

- **Frontend:** Next.js, TypeScript, React Flow, Monaco Editor
- **Backend:** FastAPI (Python), Tree-sitter / AST parsing
- **Data:** PostgreSQL + pgvector, Redis
- **AI:** OpenAI-compatible provider (swappable behind an interface)
- **Infra:** Docker Compose, OpenTelemetry, Alembic migrations

## Getting Started

```bash
# clone the repo
git clone <repo-url> && cd devonboard

# copy environment template and fill in your values
cp .env.example .env

# bring up the full stack (frontend, api, worker, postgres, redis)
docker-compose up --build
```

The `local` environment ships with seeded demo repositories and a mocked AI provider option for offline development.

## API Overview

All endpoints are versioned under `/v1`, require a bearer JWT (except health checks), and return a consistent error envelope: `{ "error": { "code", "message", "request_id" } }`.

| Method & Path | Purpose |
|---|---|
| `POST /v1/repositories/analyze` | Start analysis for a URL or ZIP (returns `202` + job id) |
| `GET /v1/repositories/{id}` | Repository status & pipeline progress |
| `GET /v1/repositories/{id}/architecture` | Architecture graph (cursor-paginated) |
| `GET /v1/repositories/{id}/files` | File tree + metadata |
| `POST /v1/repositories/{id}/trace` | Run a feature trace (SSE stream) |
| `POST /v1/repositories/{id}/ask` | Grounded Q&A — returns answer + evidence or abstains |
| `GET /v1/repositories/{id}/setup` | Generated setup guide |
| `POST /v1/repositories/{id}/validate` | Sandboxed environment validation |
| `GET /v1/repositories/{id}/tasks` | Starter tasks, filterable by difficulty |
| `GET /v1/health`, `/v1/ready` | Liveness / readiness |

## AI / RAG Grounding & Guardrails

- **Chunking:** code is chunked by symbol boundary (function/class), not fixed token windows.
- **Retrieval:** hybrid search — vector similarity narrowed by a graph lookup over code relationships.
- **Citation contract:** every `/ask` response returns `{ answer, evidence: [{file, lines, symbol}], confidence, abstained }`.
- **Abstention rule:** below-threshold confidence or no relevant chunks → the assistant says it cannot determine the answer from the repository. It never falls back to general framework knowledge.
- **Cost controls:** per-repository token budget, response caching keyed on `(commit, question hash)`.

## Security & Privacy

- **Secret redaction:** a dedicated filter scans every chunk before it leaves the trust boundary, using pattern rules plus entropy-based detection. Matches are replaced with typed placeholders (e.g. `<REDACTED_AWS_KEY>`). `.env` and credential-pattern files are excluded from indexing entirely.
- **Workspace isolation:** every analysis job runs in a disposable, network-egress-restricted container, destroyed immediately after indexing completes or the job fails.
- **AuthN / AuthZ:** JWT session auth at the gateway; every downstream query is scoped by `tenant_id`, with PostgreSQL row-level security as a second, database-level barrier.
- **Data retention:** 30-day default for analyzed repositories, with a user-triggered delete-now action that purges files, embeddings, and traces synchronously.

## Testing

| Layer | Scope | Gate |
|---|---|---|
| Unit | Parsers, redaction rules, graph builders | ≥80% coverage on analyzer & redaction modules |
| Integration | API ↔ DB ↔ worker queue | All core endpoints, success + failure cases |
| Pipeline replay | 5–8 representative demo repos | `ready` with <5% file-parse failure |
| RAG evaluation | Curated Q&A set per demo repo | ≥90% correct citation rate; 100% correct abstention |
| End-to-end | Playwright: analyze → map → trace → ask → setup → task | Green on every merge to `main` |
| Security | Redaction bypass, tenant isolation, sandbox escape | Zero known bypass before beta |

## Observability

- Structured JSON logs with `request_id`, `tenant_id`, `repository_id` on every line.
- OpenTelemetry tracing across gateway → orchestrator → analysis worker → AI service.
- Core metrics: job success rate, per-stage duration, retrieval hit rate, abstention rate, provider latency/error rate, queue depth.
- Alerting: job failure rate >5% over 15 min, queue depth growing >10 min, AI provider error rate >10%.

## Project Structure

```
devonboard/
├── frontend/           # Next.js dashboard
├── backend/
│   ├── api/             # FastAPI routes
│   ├── analyzer/        # Repository + AST analysis
│   ├── architecture/    # Graph inference
│   ├── indexing/        # Embeddings + retrieval
│   ├── ai/               # Grounded explanations
│   ├── security/        # Secret redaction, sandbox policy
│   ├── observability/   # Tracing / metrics helpers
│   └── workers/         # Background analysis jobs
├── database/            # Alembic migrations
├── tests/                # Unit, integration, pipeline-replay, RAG-eval
├── demo-repo/
├── docker-compose.yml
└── .env.example
```

## Roadmap

| Week | Phase | Exit Criteria |
|---|---|---|
| 1 | Foundation | Services run together locally and in CI |
| 1–2 | Repository scanner + AST + dependency graph | Demo corpus parses with <5% failure |
| 2–3 | Live Codebase Map | Architecture renders from real evidence |
| 3 | Feature Trace + source connection | ≥3 traced features per demo repo |
| 3–4 | RAG + AI guide, with redaction | RAG-eval gate passes at ≥90% citation accuracy |
| 4 | Setup + environment validator | Validator runs read-only checks on all demo repos |
| 4–5 | Starter task generator | Each demo repo yields ≥3 verifiable tasks |
| 5 | Security hardening + observability | Tenant isolation & tracing pass in staging |
| 6 | Polish + demo reliability | Full demo script runs twice back-to-back, no manual fixes |

## Acceptance Criteria

- A user can provide a repository and receive a real analysis result.
- Architecture is generated from actual files, imports, symbols and dependencies — never hardcoded.
- Architecture nodes open the real source files that support them.
- A feature question produces a traceable code path with animated steps.
- AI answers cite the repository files used as evidence.
- No secret or credential is ever transmitted to an external model provider unredacted.
- The system enforces per-tenant data isolation.
- P50 time-to-first-architecture-view for a repository under 5,000 files is under 90 seconds.

## License

Add your license here (e.g. MIT, Apache-2.0).
