# Engineering Platform Vision

> This document is the long-term architectural vision for the autonomous
> engineering platform. It is NOT a backlog or an execution plan. It describes
> what the platform is, what it原则, and where it is heading.

---

## Mission

Build a reusable autonomous software engineering platform capable of safely
managing many software repositories under human governance.

---

## Vision

The trading bot — the first managed project — validates the platform in
production. The platform is not the trading bot. The platform is the framework
that governs any number of software projects, with the trading bot as its
inaugural client.

The platform treats every managed project identically: it applies the same
governance discipline, the same safety gates, the same audit trails, and the
same human-approval workflows regardless of what the project does.

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

## Architecture

The platform is structured as a set of independently deployable, narrowly
scoped services. Each service has a documented interface and a bounded
responsibility.

### Major Components

```
Workflow Engine
    │
    ├── Planner          — selects the next approved task, builds an execution plan
    ├── Executor         — launches bounded agent work within allowed areas
    ├── QA               — runs automated validation with safe test flags
    ├── Reviewer         — compares evidence against acceptance criteria
    ├── Reporter         — produces structured human-readable reports
    └── Dashboard        — exposes read-only operational state and historical activity

Query Service
    └── exposes bounded read-only projections of current state, events, and history

Control Service
    └── exposes only the narrow set of approved human-authorized actions
        (pause, resume, record-approval, record-rejection)

Event Store
    └── durable, versioned, append-only log of every significant workflow transition
        and governance decision

Repository Adapters
    └── narrow interfaces wrapping project-specific Git, filesystem, and governance
        access so reusable components never touch hard-coded paths

Project Registry
    └── registry of managed projects, each with its own configuration, ownership,
        safety constraints, and allowed operation sets
```

### Component Responsibilities

**Workflow Engine** orchestrates task lifecycle. It owns state transitions,
persistence, and safety gates. It does not contain project-specific logic.

**Planner** reads the authoritative backlog and produces a concrete, bounded
execution plan including acceptance criteria, allowed areas, and risk estimates.
It never selects tasks autonomously.

**Executor** launches work through a repository-owned agent wrapper. It never
expands scope, changes safety settings, or bypasses approval.

**QA** runs only pre-configured safe test commands with forced test-mode flags.
It persists evidence, not opinions.

**Reviewer** evaluates evidence against acceptance criteria deterministically. It
never accepts work that does not fully satisfy every criterion.

**Reporter** generates structured human-readable summaries from persisted
evidence. It is the authoritative handoff artifact before human review.

**Dashboard** is read-only. It renders current project health, workflow state,
timeline, and historical activity without exposing secrets, raw agent reasoning,
or unbounded output.

**Query Service** is the shared read projection for all consumers (Telegram,
dashboard, API). It never exposes raw artifacts, environment values, or
unbounded filesystem contents.

**Control Service** is the sole mutation surface. It accepts only the narrow
set of human-authorized actions (pause, resume, record-approval,
record-rejection) and routes each through validation and audit.

**Event Store** records every workflow transition, governance decision, and
significant action as an immutable, versioned, timestamped fact. It is the
source of truth for historical timeline and audit queries.

**Repository Adapters** wrap all project-specific Git, filesystem, and
governance access behind typed interfaces. Reusable components depend only on
these interfaces, never on hard-coded paths or project names.

**Project Registry** tracks every managed project. Each project entry defines
its repository root, governance paths, owner/agent mappings, allowed
operations, prohibited operations, and project-specific safety constraints.
The trading bot is the first registered project.

---

## Managed Project Model

A repository registers with the platform by providing a typed project
configuration. Registration does not require extracting code — the platform
governs the repository in place.

A project configuration specifies:

- Project ID and display name
- Repository root
- Authoritative base branch
- Governance file paths (backlog, operating plan, owners, handoff)
- Report and artifact locations
- Safe QA commands
- Owner/agent mappings
- Prohibited operations (e.g., no live trading, no secret access)
- Whether agents may merge

Platform services consume this configuration rather than hard-coding
"trading-bot" paths, commands, or assumptions.

Future managed projects can be added by creating a new project configuration
entry — no platform code changes required.

---

## Future Capabilities

These capabilities are enabled by the architecture but not yet implemented.
They require separate planning, narrow implementation slices, and explicit
Josh approval before work begins.

### Multiple Managed Repositories

The Project Registry and repository adapter architecture already support
managing more than one repository. A second repository can be added by
registering it — no code extraction required.

### Multiple Concurrent Agents

The bounded workflow engine can track multiple simultaneous task workflows
across different projects. Each task remains isolated: its state, evidence,
and outcome do not interfere with another task's.

### Distributed Workers

Workers can run on separate machines with shared event/outbox storage.
Horizontal scaling is a future deployment concern, not a current requirement.

### Richer Engineering Analytics

The event store records structured facts about every workflow transition,
test run, review, approval, and governance decision. These facts can power
dashboards showing cycle time, review turnaround, test reliability, and
other engineering metrics.

### Historical Timeline

A bounded timeline renders the complete history of engineering activities
across all managed projects: task creation, agent delegations, test runs,
reviews, approvals, commits, PR activity, failures, and merges.

### Approval Workflows

Human approval gates can be extended to cover more governance surfaces:
architecture reviews, security reviews, deployment gates, and rollback
triggers.

### Plugin/Adaptor Ecosystem

Repository adapters are narrow interfaces. A future adapter ecosystem could
support GitHub, GitLab, Bitbucket, Jira, Linear, Slack, PagerDuty, and other
tools without changing the core workflow engine.

### Project Templates

A managed project template could bootstrap a new repository with the full
governance structure, workflow engine, event store, and dashboard already
configured.

### Reusable Governance Packs

Governance packs — predefined backlog templates, safety constraint sets,
allowed-area patterns, and review checklists — can be applied to new
projects as configuration rather than custom implementation.

---

## What This Platform Is Not

The platform is **not** specific to trading software. Trading-bot logic,
brokerage integrations, strategy settings, and trading dashboards are
application-specific concerns managed by the trading-bot project. They live
in the trading-bot repository, not in the platform.

The platform does **not** run agents without human approval gates. Every
task requires a human to select it, review its plan, and accept its report.

The platform does **not** execute arbitrary code. Every agent run is bounded:
a defined scope, allowed file paths, prohibited operations, and a
pre-configured QA command.

The platform does **not** expose secrets, credentials, raw agent reasoning,
unbounded filesystem contents, or internal errors to any surface (Telegram,
dashboard, API) without sanitization.

The platform does **not** merge automatically. Human review and explicit
approval are always required before a change reaches the base branch.

---

_Last updated: 2026-08-05_
