# Owner setup (pending)

These steps activate governance after code validation. They can wait until morning.
Do not paste tokens or private key contents into chat or tracked files.

## 1. HCP Terraform

1. Sign in at <https://app.terraform.io> and create the organization `abdel-abdelaal`
    (if unavailable, update versions.tf through a PR to use the actual name).
2. Create workspace `github-governance`, choose CLI-driven workflow, and set
    Settings → General → Execution Mode to **Local**. Actions runs Terraform;
    HCP stores and locks state.
3. User Settings → Tokens → Create an API token. Set it privately:
    `gh secret set TF_API_TOKEN -R Abdel-Abdelaal/github-governance`.

## 2. Governance GitHub App

GitHub Settings → Developer settings → GitHub Apps → New GitHub App.
Use a unique name based on `abdel-governance-bot`, disable webhooks, and restrict
installation to this account. Install on the four showcase repos only.

Repository permissions: Administration read/write, Contents read/write (required
even for provider reads), Issues read/write, Pull requests read/write, Environments
read/write, Metadata read, Secrets read (names and dates, not secret values).

Set GOV_APP_ID as a repo secret and use `gh secret set GOV_APP_KEY -R
Abdel-Abdelaal/github-governance < private-key.pem` in a shell that supports input
redirection. In PowerShell, use `Get-Content -Raw -LiteralPath <path> | gh secret set
GOV_APP_KEY -R Abdel-Abdelaal/github-governance`. Never commit the key.

## 3. Automation GitHub App

Create a second App based on `abdel-automation-bot`: Contents, Pull requests,
Issues and Workflows read/write; Metadata read. Install only on the showcase repos.
Set BOT_APP_ID and BOT_APP_KEY as repository secrets on all four. This identity
opens bot PRs that trigger normal CI, without granting administration permission.

## 4. Bootstrap and verify

If the reviewed one-time local bootstrap was used, first migrate its verified
private state to HCP with Terraform's supported state migration flow. Retire the
local backend after migration; do not run independent local and hosted applies.
The staging state lives under D:/showcase/.state and is never committed.

Run Terraform workflow manually on main. Inspect the first hosted plan before applying:
imports must adopt all four repositories, their protect-main rulesets and labels;
no repository deletion or replacement is acceptable. Then run a second plan and
require exit code 0. Do not claim governance active before this succeeds.

Test drift with a harmless temporary wiki setting change in shortlink, then run
the plan workflow and check that `Drift detected` opens. Run apply to restore the
declared setting and confirm the next plan closes the issue. Record links in README.

## What each step unlocks

| Step | Unlocks | Until then |
| --- | --- | --- |
| HCP Terraform + governance App | hosted plan on PRs, apply on merge, nightly drift issue, pending desired checks (`conformance / Conformance`, shortlink `Build, smoke and scan`) and new labels applied | Terraform workflow validates only; Fleet health lists the pending checks as "not applied" |
| Governance App alone | complete monthly access review (secret ages, bypass actors, deploy keys) | those sections show "skipped" |
| Automation App | baseline sync PRs, release-please release PRs, stale mutations, AI draft fix PRs | each of those steps skips with a notice |
| SonarQube Cloud | duplication and maintainability quality gate on shortlink | Ruff, Pyright and coverage gates still apply |
| Hosting choice | public deployment of the already-attested shortlink image | image is published to GHCR, not served |

## Later services

- SonarQube Cloud: GitHub sign-in, import shortlink, disable Automatic Analysis,
  create a token in My Account → Security, and set SONAR_TOKEN on shortlink.
  Read the actual organization and project key from its UI before configuring CI.
- Deployment: owner chooses the target. Container build and smoke-test code can
  be prepared first; no hosting subscription or live deploy is implied.

## Moving to a GitHub organisation

`Abdel-Abdelaal` is a personal account. Personal accounts have no account-wide
rulesets and no required workflows, so a new repository starts with no protection
and no CI until someone onboards it. Here that gap is closed by detection, not
enforcement: [Fleet health](../.github/workflows/fleet-health.yml) runs daily, lists
every public repository missing from `repos.yaml` as `unmanaged`, and flags managed
repositories missing the handbook's required files or platform-workflows callers.
Onboarding stays a PR: copy the new-repo starter from
[`.github/baseline/new-repo/`](https://github.com/Abdel-Abdelaal/.github/tree/main/baseline/new-repo),
then add the repository to `repos.yaml`. The governance and automation Apps are
installed on selected repositories, so add the new one to both installations too.

An organisation would enforce this on every new repository automatically:

- **Organisation rulesets** target repositories by name pattern (for example `*`)
  or custom property, so protect-main (PRs only, required checks, no force push or
  deletion) applies from the moment a repository is created. GitHub documents
  organisation rulesets for the Team and Enterprise plans.
- **Required workflows** (the "Require workflows to pass before merging" ruleset
  rule) run `pr-title`, `security` and `conformance` from platform-workflows on
  every targeted repository's PRs, with no caller file in that repository. GitHub
  documents this rule for Enterprise Cloud.
- **Custom properties** (for example `type: service`) let rules and required checks
  differ by repository type instead of being listed per repository.
- **Organisation secrets and variables** (`AI_API_KEY`, `BOT_APP_ID` and so on) are
  set once and shared with selected repositories.
- **Apps installed on all repositories** cover new repositories without a manual
  installation step.

| | Personal account (today) | Organisation |
| --- | --- | --- |
| New repository protection | none until onboarded; Fleet health flags it within a day | from creation, by organisation ruleset |
| CI on a new repository | none until the starter's callers are copied in | required workflows run on PRs; scheduled jobs (Scorecard, weekly security) still need callers |
| Cost | free | organisation rulesets need a paid plan; required workflows need Enterprise Cloud |
| Terraform | `repos.yaml` plus per-repository rulesets | adds `github_organization_ruleset`; the provider owner changes |
| Migration | none | transfer each repository, then update every `Abdel-Abdelaal/...` reference: caller pins, `OWNER` in `tools/github_api.py`, the Terraform owner, badges, Scorecard URLs, HCP and App installations. Don't rely on transfer redirects for `uses:` references. |
| Identity | repositories sit on Abdel's profile | repositories sit under the organisation, with Abdel as owner |
| Approvals | solo, zero required approvals | still zero for a solo owner; a team could require 1–2 plus CODEOWNERS review |

Fleet health stays useful after a move. It checks files and callers that rulesets
don't, and keeps the evidence in one issue.
