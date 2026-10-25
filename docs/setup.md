# Owner setup (pending)

These steps activate governance after code validation. They can wait until morning.
Do not paste tokens or private key contents into chat or tracked files.

## 1. HCP Terraform

1. Sign in at https://app.terraform.io and create the organization `abdel-abdelaal`
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
|---|---|---|
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
