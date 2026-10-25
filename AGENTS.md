# AGENTS.md

Terraform governance for the public repositories declared in repos.yaml. Follow
https://github.com/Abdel-Abdelaal/.github/tree/main/standards.

## Commands

terraform fmt -check -recursive; terraform init -backend=false; terraform validate.
Privileged plans and applies run only after account setup. Never claim an apply
succeeded when credentials are missing. Never expose plan/state credentials.

## Rules

All settings changes use a PR. Keep prevent_destroy on repository resources.
Never delete or recreate a managed repo. Do not disable protections for convenience.
Existing repositories added to inventory need import before a reviewed apply.
Never publish private repository identities or payloads in this public inventory.
Pinned actions, least privilege, harden-runner first, timeout-minutes on every job.
AI can open PRs but cannot merge or apply infrastructure.

## Glossary

| Name | Meaning |
|---|---|
| desired state | settings declared in repos.yaml and Terraform |
| drift | a difference between desired state and GitHub settings |
| fleet | the repositories declared in repos.yaml |
| unmanaged | a public, non-fork, non-archived repository missing from repos.yaml; Fleet health lists it |
