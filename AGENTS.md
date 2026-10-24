# AGENTS.md

Terraform governance for only the four repositories in repos.yaml. Follow
https://github.com/Abdel-Abdelaal/.github/tree/main/standards.

## Commands

terraform fmt -check -recursive; terraform init -backend=false; terraform validate.
Privileged plans and applies run only after account setup. Never claim an apply
succeeded when credentials are missing. Never expose plan/state credentials.

## Rules

All settings changes use a PR. Keep prevent_destroy on repository resources.
Never delete or recreate a managed repo. Do not disable protections for convenience.
Pinned actions, least privilege, harden-runner first, timeout-minutes on every job.
AI can open PRs but cannot merge or apply infrastructure.

## Glossary

| Name | Meaning |
|---|---|
| desired state | settings declared in repos.yaml and Terraform |
| drift | a difference between desired state and GitHub settings |
| fleet | the four explicitly managed showcase repositories |
