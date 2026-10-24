---
status: accepted
date: 2026-10-24
---

# Use Dependabot and shared security checks

## Context and problem statement

Dependency vulnerabilities and compromised workflow actions can appear without a
code change. The same checks must protect the service and its delivery platform.

## Considered options

- Manual updates and ad hoc scans.
- Renovate plus an external bot identity.
- GitHub-native Dependabot with shared security workflows.

## Decision outcome

Use Dependabot for GitHub Actions and each repository's actual ecosystem, with
weekly grouped patch/minor updates and a 14-day cooldown. Security alerts and
security-fix PRs are enabled separately; urgent security fixes must not wait for
the normal update cadence. CodeQL, gitleaks, actionlint and zizmor run on PRs,
main and a weekly schedule. Scorecard remains a local workflow so publication
can use its supported identity constraints.

### Consequences

- Dependency review is warn-only until 2026-10-31, then enforces automatically.
- Patches/minors may enable auto-merge only once the owner or governance sets
  AUTOMERGE_ENABLED=true after required quality/security checks are active.
  It is deliberately disabled during incomplete governance bootstrap.
- Majors require human review. AI checks never become required checks.
- Secrets and workflow scanning are immediate, not part of the warning window.
- All remote workflow calls, including our own, are SHA-pinned with version
  comments. Dependabot delivers reviewed release updates instead of a moving tag. Online scanning checks the references for known compromises.
