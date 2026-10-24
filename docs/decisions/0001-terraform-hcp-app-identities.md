---
status: accepted
date: 2026-10-24
---

# Manage the showcase with Terraform and separate GitHub Apps

## Context and problem statement

Settings must be reviewed, repeatable and checked for drift, including the settings
that protect this governance repo. Broad personal tokens would couple automation
to the owner's account and be difficult to limit and rotate.

## Considered options

- Manual GitHub settings with scripts.
- Terraform with local state.
- Terraform with HCP state and separate GitHub Apps.

## Decision outcome

Use the integrations/github provider and HCP Terraform in Local execution mode.
Actions execute plans/applies; HCP supplies state history and locking. Separate
governance and automation Apps limit administration permission to governance.
Account setup remains pending; offline validation does not claim live management.

The label source is pinned to a reviewed handbook commit until baseline sync
lands. Reading moving main at plan time could change an approved plan's inputs.

### Consequences

- Settings changes have a reviewable diff and import history.
- Account/App setup and secret rotation remain owner responsibilities.
- Missing credentials skip privileged work with an explicit summary.
- PR source is untrusted: fork and Dependabot runs never obtain privileged keys.
- Repositories cannot be destroyed by this module. Future environment resources
  must be added in their own reviewed change before deployments are enabled.
