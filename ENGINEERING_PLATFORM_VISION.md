# Engineering Platform Vision

> This document is the long-term architectural vision for the autonomous
> engineering platform. It is NOT a backlog or an execution plan. It describes
> what the platform is, what it stands for, and where it is heading.
>
> This is a durable document. It should be revised when strategic direction
> changes, not when implementation details change. Implementation guidance lives
> in the backlog.

---

## Mission

Build a reusable autonomous software engineering platform capable of safely
managing many software repositories under human governance.

---

## Vision

The trading bot is the first managed project — not the product itself. The
platform is the product. It validates the platform in production; the platform
remains project-agnostic and ready to govern any software repository.

Every future decision about architecture, components, and trade-offs should
optimize for a reusable platform first, while keeping the trading bot working
as the reference managed project.

---

## Core Principles

| Principle | Meaning |
|---|---|
| Human approval gates | No automated change merges without explicit human sign-off. |
| Fail closed | Unknown inputs, missing data, and ambiguous states stop safely rather than proceeding. |
| Read before write | Every agent reads existing governance and architecture before modifying anything. |
| Safety before autonomy | Autonomy is earned through evidence, not assumed. Safety constraints are never relaxed for speed. |
| Project-agnostic architecture | No hard-coded trading-bot paths, names, or assumptions exist in reusable platform components. |
| Complete auditability | Every significant action is recorded, timestamped, and retrievable. |
| Deterministic governance | The same input always produces the same governance decision. |
| Least privilege | Agents receive the minimum permissions required for each task. |
| Explicit allowed areas | Every implementation task lists exactly which files may change. |
| Small, reviewable changes | Prefer focused commits over broad rewrites. |
| No silent behavior changes | Any behavioral difference must be explained, not hidden. |

---

## Safety Philosophy

These constraints are non-negotiable. They apply to every managed project,
every agent run, and every workflow state.

- **No autonomous merges** — every change requires human approval.
- **No live trading** — trading bot paper mode is always enforced.
- **No secret modification** — credentials and secrets are never touched by agents.
- **Fail closed** — ambiguous, unknown, or missing inputs stop rather than proceed.
- **Human approval gates** — every significant state transition requires explicit human authorization.
- **Explicit allowed areas** — agents may only touch files explicitly listed in their task.
- **Least privilege** — agents receive only the permissions their current task requires.
- **Auditable behavior** — every action leaves a timestamped, retrievable record.
- **Deterministic workflows** — the same workflow state always produces the same outcome.

---

## Dashboard Philosophy

Observability before orchestration. Read-only before control.

- **Engineering dashboard first** — all stakeholders see the same authoritative state.
- **Read-only before controls** — visibility is a prerequisite for safe control.
- **Data integrity before visualization** — dashboards must not lie to seem more complete.
- **Visualization before orchestration** — understand before acting.
- **Control only after trustworthy observability** — controls are gated behind stable, tested read surfaces.

---

## Engineering Culture

How the platform team works is as important as what the platform does.

- **Prefer evolution over rewrites** — small incremental improvements over large refactors.
- **Adapters before forks** — solve cross-project concerns through interfaces, not duplication.
- **Read models before control models** — understand what exists before deciding what should change.
- **Dashboards before automation** — observe first; act only when observation is reliable.
- **Observability before orchestration** — you cannot govern what you cannot see.
- **Deterministic governance** — same facts, same decision, every time.
- **Bounded autonomous execution** — agents may act only within explicitly approved scope.
- **Every action leaves evidence** — nothing is governance-relevant without a record.
- **Every mutation is reviewable** — nothing merges without a human reviewing what changed.
- **Every project is replaceable** — managed projects are clients, not dependencies.
- **Reusable components preferred over project-specific logic** — shared code is maintained once.

---

## Architecture

### Platform Components

The platform is structured as a set of narrowly scoped, independently deployable
services. Each service has a documented interface and a bounded responsibility.
No service imports the runtime code of a managed project.

```
Platform
├── Dashboard            — read-only engineering health, workflow state, and historical activity
├── Query Service        — bounded read projection of current state, events, and history
├── Control Service      — narrow authorized mutations only (pause, resume, record-approval, record-rejection)
├── Workflow Engine      — orchestrates task lifecycle; owns state transitions and safety gates
├── Planner              — selects next approved task; builds bounded execution plan
├── Executor             — launches bounded agent work within allowed areas
├── QA                  — runs pre-configured safe test commands with test-mode flags
├── Reviewer             — compares evidence against acceptance criteria deterministically
├── Reporter             — produces structured human-readable reports from persisted evidence
├── Event Store          — durable, versioned, append-only log of workflow transitions and governance decisions
├── Repository Adapter   — narrow typed interface wrapping project-specific Git and filesystem access
├── Project Registry     — registry of managed projects with their configuration and safety constraints
├── Git Adapter          — narrow interface for Git operations (used by repository adapter)
└── GitHub Adapter       — narrow interface for GitHub API operations (PR status, metadata)
```

### Managed Projects

```
Managed Projects
├── Trading Bot          — first managed project; validates the platform in production
│   └── adapters         — trading-bot-specific repository, Git, and governance adapters
└── Future Repositories  — additional managed projects governed through project configuration
    └── adapters         — future project-specific adapters implementing the same contracts
```

### Architectural Constraints

- The platform must never require importing a managed project's runtime code directly.
- Projects are integrated through adapters and typed contracts, not through shared code.
- Platform components may not mutate outside their explicitly allowed scope.
- Every adapter call is audited; every adapter is testable in isolation.
- The workflow engine is the only orchestrator; no component may start another component's work.

---

## Managed Project Model

A repository registers with the platform by providing a typed project configuration.
Registration does not require extracting code — the platform governs the repository in place.

### Project Configuration Contract

Each managed project exposes the following architectural contract (format TBD;
this defines the required data, not the file format):

| Field | Description |
|---|---|
| Project identity | Unique project ID and human-readable display name |
| Repository root | Absolute path or remote URL for the managed repository |
| Authoritative base branch | The branch all feature branches derive from (e.g., `main`) |
| Governance files | Paths to the project's backlog, operating plan, owners, and handoff documents |
| Workflow files | Paths to the project's workflow engine, state handlers, and persistence |
| QA commands | Pre-configured safe test commands the platform may run on this project |
| Safety constraints | Project-specific prohibited operations (e.g., no live trading, no secret access) |
| Agent permissions | Per-owner/per-agent allowed areas and operation sets |
| Report locations | Where engineering reports and artifacts are stored |
| Approval policy | How human approval is routed and recorded for this project |
| Owner mapping | Maps human owners to agent identities for this project |
| Project adapters | Which adapter implementations this project uses for Git, repository, and governance access |

The platform consumes this contract to govern the project. The project does not
import the platform.

---

## Project Lifecycle

The platform evolves through a staged maturity path. Each phase must be
validated before the next begins.

### Phase 1 — Single Managed Repository

The trading bot is governed by the platform in a single repository. All
governance, workflow, event, and reporting infrastructure runs in the trading-bot
repository. No extraction has occurred.

**Entry criteria**: Platform workflow runs end-to-end with human approval gates,
event store, query service, and read-only dashboard.

### Phase 2 — Reusable Engineering Platform

Core platform components are structured as reusable, project-agnostic code.
The trading bot remains the only managed project, but platform code no longer
contains trading-bot-specific hard-coded paths or assumptions.

**Entry criteria**: Project configuration contract exists; at least one adapter
has been extracted; trading-bot platform code contains no project-specific
hard-coded paths.

### Phase 3 — Multiple Managed Repositories

A second repository registers with the platform using the project configuration
contract. No code is shared between repositories except the platform components.
Both repositories are governed independently.

**Entry criteria**: Second repository successfully registers using project
configuration; both repositories are governed without modifying platform code.

### Phase 4 — Concurrent Engineering Agents

Multiple simultaneous task workflows run across one or more managed projects.
Each task remains isolated: its state, evidence, and outcome do not interfere
with another task's. The workflow engine coordinates all concurrent activity.

**Entry criteria**: Two or more tasks run concurrently across managed projects
with correct isolation and audit trails.

### Phase 5 — Distributed Workers

Workers run on separate machines with shared event/outbox storage. Horizontal
scaling is a deployment concern; governance and audit semantics remain unchanged.

**Entry criteria**: Workers on separate hosts successfully share event store
and outbox; governance semantics are identical to single-host operation.

### Phase 6 — Platform Extraction

The platform is extracted into its own repository, distinct from any managed
project. Managed projects consume the platform as a dependency through a
versioned adapter contract. The trading-bot repository becomes a managed
project, not the platform itself.

**Entry criteria**: Platform repository exists independently; trading bot
registers as a managed project; no managed project imports platform runtime code
directly.

---

## Repository Extraction Strategy

Extraction is a consequence of stable interfaces, not an objective by itself.

The engineering platform will **not** be extracted until all of the following
are true:

- Project registration (`ENGPLAT-001`) exists and has proven stable through normal use.
- Repository adapters (`ENGPLAT-002`) exist and have proven the interface contract.
- Dashboard boundaries (`ENGDASH-005`, `ENGDASH-006`) are stable and tested.
- Several complete engineering cycles have validated the platform without incident.

Cross-repository versioning, deployment, authentication, and migration require
separate planning and human approval before extraction begins.

---

## Future Capabilities

These capabilities are enabled by the architecture but not yet implemented.
Each requires separate planning, a narrow implementation slice, and explicit
Josh approval before work begins.

| Capability | Description |
|---|---|
| Multiple managed repositories | Project Registry and adapter architecture already support this; adding a repository is a configuration change, not a code change. |
| Concurrent engineering agents | Workflow engine already tracks task isolation; concurrent tasks require shared event store and bounded coordination. |
| Distributed workers | Horizontal scaling is a deployment concern; governance semantics remain identical. |
| Richer engineering analytics | Event store records structured facts about every workflow transition; these power cycle-time, review-turnaround, and test-reliability dashboards. |
| Historical timeline | Complete history of engineering activities across all managed projects: task creation, agent delegations, test runs, reviews, approvals, commits, PR activity, failures, and merges. |
| Approval workflows | Architecture review gates, security review gates, deployment gates, and rollback triggers — each requiring separate planning. |
| Plugin/adaptor ecosystem | Future adapters for GitHub, GitLab, Bitbucket, Jira, Linear, Slack, PagerDuty, and other tools without changing core workflow engine. |
| Project templates | Bootstrapping a new managed project with full governance structure, workflow engine, event store, and dashboard already configured. |
| Reusable governance packs | Predefined backlog templates, safety constraint sets, allowed-area patterns, and review checklists applicable to new projects as configuration. |

---

## What This Platform Is Not

The platform is **not** specific to trading software. Trading-bot logic,
brokerage integrations, strategy settings, and trading dashboards are
application-specific concerns that live in the trading-bot repository, not in
the platform.

The platform does **not** run agents without human approval gates. Every task
requires a human to select it, review its plan, and accept its report.

The platform does **not** execute arbitrary code. Every agent run is bounded:
a defined scope, allowed file paths, prohibited operations, and a
pre-configured QA command.

The platform does **not** expose secrets, credentials, raw agent reasoning,
unbounded filesystem contents, or internal errors to any surface (Telegram,
dashboard, API) without sanitization.

The platform does **not** merge automatically. Human review and explicit
approval are always required before a change reaches the base branch.

---

## Future Managed-Project Configuration (Architectural Note)

> This section is a non-executable architectural note. It describes what every
> managed project will eventually expose to the platform. It does not authorize
> implementation, migration, or deployment.

Every managed project will eventually provide the following through its project
configuration:

```
Repository metadata
  - project_id: unique identifier
  - display_name: human-readable name
  - repository_root: absolute path or remote URL
  - authoritative_base_branch: base branch for all feature work

Governance locations
  - backlog_path: path to the authoritative backlog
  - operating_plan_path: path to the agent operating plan
  - owners_path: path to the owners file
  - handoff_path: path to the engineering handoff document

QA commands
  - safe_test_command: pre-configured pytest command with test-mode flags
  - safe_lint_command: pre-configured lint command (if applicable)
  - qa_timeout_seconds: maximum QA runtime

Safety policy
  - prohibited_operations: list of explicitly banned operations
  - allowed_agents: which agent identities may operate on this project
  - merge_requires_approval: boolean — always true

Approval policy
  - approval_channel: where approval requests are routed
  - approval_timeout_hours: how long an approval request remains open
  - escalation_policy: what happens if approval is not granted

Project adapters
  - repository_adapter: which adapter implementation this project uses for Git/filesystem
  - governance_adapter: which adapter implementation this project uses for governance access
  - reporting_adapter: which adapter implementation this project uses for reports/artifacts

Owner mapping
  - human_owners: list of human owners with their communication channels
  - agent_owners: list of agent identities and their approved scopes
```

The purpose of this contract is to eliminate all hard-coded "trading-bot"
assumptions from the platform. The platform never hard-codes a project name,
path, command, or identity. Everything is supplied through the project
configuration.

---

_Last updated: 2026-08-05_
