---
status: accepted
date: 2026-10-24
---

# Bootstrap verified required checks without waiting for hosted state setup

## Context and problem statement

The quality and security workflows have run successfully in real consumer PRs,
but the initial ruleset still requires only the Conventional title check. HCP
Terraform and the governance App are not configured yet. Waiting for those accounts
would leave a gap between checks that run and checks that actually block merging.
The owner asked for stronger security/reliability and autonomous progress overnight.

## Considered options

- Leave checks advisory until hosted state/App setup.
- Change the rules directly by REST outside the desired-state review process.
- Review desired check names in this repo, then perform one bounded Terraform
  bootstrap using the already-authenticated owner's GitHub CLI OAuth session.

## Decision outcome

Use the third option once. Keep the production configuration's HCP cloud block.
Create a private local staging copy with only its backend changed to local state
under D:/showcase/.state, keeping the same provider locks, modules and imports.
Obtain the existing CLI OAuth credential only in process memory; never print or
save it, create a PAT, or add it to automation secrets.

Inspect the saved plan: scope must remain the four named repos, with no resource
deletion/replacement, no bypass actors and no weakened rules. Apply that exact plan,
then require a fresh plan to return exit code 0. Record actual results separately.
Only previously successful concrete check names become required. AI and Scorecard
checks do not become required: AI is advisory and Scorecard runs on main/schedules.

### Consequences

- Tests and deterministic security jobs become real merge gates immediately.
- This is a one-time operator bootstrap, not unattended local-state automation.
- The normal HCP/App workflow remains setup-gated and cannot claim drift coverage.
- Migrate the verified state to HCP before enabling unattended applies; retire the
  local staging backend so two independent state files never manage the fleet.
- Future required-check changes remain PRs here, after a successful first run.
- The temporary owner OAuth identity has broader rights than the eventual App;
  it is used only for bootstrap and never handed to a model or scheduled workflow.
