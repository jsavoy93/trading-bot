# Engineering Control-Surface Proposals

These documents define candidate work only. Their presence does not authorize
implementation, branch creation, service startup, credential use, deployment,
or any external action.

## Governance rule

No new backlog item may begin implementation until:

1. its proposal markdown exists,
2. Josh has reviewed it,
3. Josh has explicitly approved it,
4. the item has been added to `AGENT_BACKLOG.md`,
5. a dedicated feature branch has been created.

Each gate is mandatory and sequential. Approval of this proposal collection or
its pull request is documentation approval only; it is not implementation
approval for OPS-015, DASH-007, or OPS-016.

## Proposed sequence

1. [OPS-015](OPS-015.md) — allowlisted Telegram engineering adapter.
2. [DASH-007](DASH-007.md) — read-only engineering dashboard.
3. Operational soak of event/outbox delivery, restart, stale, and duplicate
   behavior.
4. [OPS-016](OPS-016.md) — audited engineering approval actions, only after a
   separate threat-model review and explicit approval.

CONFIG-001 remains paused. No proposal permits live trading, direct control of
the interactive Codex TUI, merging, pushing to `main`, deployment, or secret
modification.
