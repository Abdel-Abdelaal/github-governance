# Public AI observatory

The static dashboard is published by `ai-telemetry-dashboard.yml` every six hours
and on demand. It reads the managed fleet from `repos.yaml`. Only public monthly
telemetry Release assets matching the record naming convention are considered.
Release and asset endpoints are both paginated. Each record must verify against
its Sigstore bundle, repository identity, the platform reusable signer workflow
and the custom record predicate before it enters the dataset.

`metrics.json` contains repository/mode aggregates and collection errors.
`records.jsonl` contains verified records, their source URLs and SHA-256 digests.
`badge.json` provides a Shields endpoint badge. Collection failures are visible
alongside partial results; no historical numbers or sample measurements are added.
Empty datasets show an explicit empty state.

The output is rebuilt from durable source assets, so Pages artifact retention does
not define dataset retention. Deletion of a source asset remains an availability
risk. Signature verification detects modification, not deletion or AI correctness.

Success means validated output divided by attempted runs, excluding credential
skips. Latency sums all attempts per run. Token counts are sums of reported usage;
the dashboard shows how many runs supplied usage. Findings count as published only
when a publisher receipt says so. Outcome quality, red-team rates and non-platform
AI integrations remain explicitly unavailable until instrumented.
Partial publication receipts count confirmed summary findings even if a later
check API call failed. Damaged execution/publisher evidence is counted separately
and produces a visible coverage warning.

The dashboard has no external JavaScript dependencies. Model text is downloadable
as data but never rendered as HTML. UI labels use DOM text content. The site has a
restrictive Content Security Policy, accessible labels and mobile layouts.

Run offline validation with `python -m unittest discover -s tests -v`.
For collection, convert `repos.yaml` to JSON, then run
`python tools/ai_telemetry_dashboard.py --repos repos.json --output SITE_DIRECTORY`.
The GitHub CLI performs signature verification; its own token remains in the
collector environment and is never sent to model processes or public downloads.
