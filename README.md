# Akshit Job Search Automation

A transparent, human-in-the-loop job finder tailored to Akshit Didla's verified background. It collects public openings from ATS career pages, public job-board feeds, and optional aggregators; checks eligibility; assigns an automated screening score; removes duplicates; verifies application pages; and writes Markdown/CSV/JSON reports.

## Search priority

Eligible full-time roles across India and global/remote roles that explicitly support hiring in India are accepted. Location is primarily an eligibility check, not a hiring-fit bonus. When otherwise comparable roles have the same screening score and freshness, remote roles and Hyderabad are preferred as tie-breakers; Bengaluru and other Indian locations are not downgraded in the score.

Remote internships require explicitly advertised compensation of at least INR 40,000 per month. Onsite or hybrid internships must pay more than INR 40,000 per month. The current parser recognizes INR compensation only.

Location-restricted remote roles outside India are excluded. The tool never applies automatically.

## Candidate basis

The profile uses Akshit's verified background through April 2026: approximately 39 months from the recorded non-overlapping dates, with 42 months retained separately as the user's reported presentation total. Relevant work covers data verification, market research, CRM data maintenance, sales support, and lead generation. The strongest evidence is:

- 5,000+ U.S. healthcare-provider records verified against official licensing sources
- duplicate resolution, record merging, missing-data research, and QA review of 100-200 record batches
- Salesforce and HubSpot use for CRM data maintenance and support, not administration or systems ownership
- basic operational Excel/Google Sheets reporting and dataset work, not advanced Excel
- U.S.-focused team support and comfort with evening/night shifts
- professional use of Excel, Google Sheets, Apollo.io, ZoomInfo, SalesIntel, LinkedIn Sales Navigator, and 6sense

SQL, Python, Power BI, Microsoft 365, and AI automation are learning areas, not established professional proficiency. The profile does not claim RevOps ownership, forecasting, billing, quota administration, automation ownership, SaaS metrics experience, advanced HubSpot ownership, or professional software/data engineering.

## Target roles

- Primary: Business Operations, Operations Analyst/Associate, GTM Operations, operational Sales Operations, CRM Operations/CRM Data, Data Quality/Data Operations, Market/Business Research, Research Operations, Implementation/Product Operations, Customer/Partner Operations, and junior non-coding AI workflow/process operations
- Excluded or strongly penalized: cold calling, SDR/BDR, outbound prospecting, meeting-setting, quota-carrying sales, recruiter-style outbound work, finance/accounting-heavy RevOps, software/data engineering, and roles where advanced SQL, Python, Power BI, Excel, forecasting, billing, quota administration, or advanced HubSpot ownership is mandatory

The preferred experience band is 0-4 years. Roles requiring exactly 5 years may be retained as stretches; roles requiring more than 5 years are rejected.

## Compensation quality

The primary full-time target is INR 10 LPA or a realistic equivalent. Unpublished compensation does not reject an otherwise strong role. Employer-posted compensation below the target is visibly labelled and lowers the screening score. Aggregator estimates are shown as estimates but do not affect the score. Career value is a separate human-review signal and never offsets weak compensation or changes Actual Hiring Fit.

Listings are labelled fresh (up to 7 days), recent (8-30 days), older, or unknown. Freshness affects ordering after screening score; an older vacancy remains eligible when it is officially active. Verified-active jobs scoring at least 45 can enter the **Strong shortlist** only when no exclusion applies and evidence is not insufficient. Jobs scoring 30–44, and higher-scoring jobs that remain unverified or evidence-thin, enter the **Review queue**. Jobs below 30 or carrying hard-exclusion signals are suppressed. Confirmed closed roles are removed.

## Scores and assessment status

`screening_score` is an automated prioritization signal based on title/function relevance, verified candidate-skill overlap, domain compatibility, seniority, evidence quality, experience, and compensation. Exclusion rules operate separately and always override the score. It is not Actual Hiring Fit. ATS similarity and Actual Hiring Fit remain unassessed in Phase 1. They are stored as null/not-assessed and must not be inferred from the screening score.

Actual Hiring Fit will be added only through the separately tested evidence-based Phase 2 engine, including the approved mandatory-gap caps.

## Quick start

1. Edit `config/profile.json` if needed.
2. Add or remove ATS company identifiers in `config/sources.json`.
3. Run:

```powershell
python -m job_search run
```

Use `python -m job_search run --dry-run` to collect and print a diagnostic without changing tracked reports or `data/seen_jobs.json`.

Reports are written to `output/latest.md`, `output/jobs.csv`, and `output/jobs.json`. Every run shows all currently active matches and labels first-seen jobs `NEW`. Previously seen IDs are retained in `data/seen_jobs.json`.

Before writing reports, the collector checks each non-suppressed application page. HTTP 404/410 responses and explicit closed, expired, filled, removed, or no-longer-accepting messages are removed. Pages with a recognizable application action, such as **Apply now** or **Submit application**, may enter the strong shortlist when the score, evidence, and exclusion rules also allow it. Blocked, inconclusive, or evidence-thin pages remain in the review queue.

## Future application-state design

Private application tracking is intentionally not implemented in Phase 1. The planned design has two layers: a non-sensitive public suppression/fingerprint mechanism that scheduled automation can use to avoid re-recommending dispositioned jobs, and private local state containing the actual status, notes, and personal application history. Private statuses such as applied, rejected, interview, offer, and not-pursuing must never be committed to the public repository or included in public reports or Slack notifications.

## Sources

- ATS: Greenhouse, Lever, Ashby
- Public job boards: Remote OK, Remotive, Arbeitnow, Himalayas, The Muse
- Optional aggregators: Adzuna and Jooble
- Private companion automation: Gmail alerts from LinkedIn, Naukri, Indeed, Foundit, Glassdoor, Wellfound, and Instahyre

Gmail alert links are intentionally never written to this public repository because some contain personalized authentication or tracking tokens.

### Optional GitHub secrets

Add these under **Settings > Secrets and variables > Actions**:

- `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` from [Adzuna Developer](https://developer.adzuna.com/)
- `JOOBLE_API_KEY` from [Jooble API](https://jooble.org/api/about)
- `THEMUSE_API_KEY` from [The Muse API](https://www.themuse.com/developers/api/v2) (recommended for a higher rate limit)

Missing optional credentials do not fail the workflow; those sources remain disabled until configured.

### Optional Slack alerts

Slack is optional and only sends a concise notification when the public-source report contains NEW matches or real source failures. It never sends Gmail-derived job-alert data. To enable it, create a Slack Incoming Webhook for your chosen private channel and add its URL as the Actions repository secret `SLACK_WEBHOOK_URL`. If the secret is absent, the notification step safely skips.

Run the tests with:

```powershell
python -m unittest discover -s tests -v
```

## GitHub Actions

The included workflow is scheduled every weekday at 09:00 and 17:00 IST (03:30 and 11:30 UTC), runs immediately after automation code/config changes reach `main`, and can also be started manually. It commits updated reports and deduplication state back to the repository. Every run places the full report in the GitHub Actions summary. GitHub cron is best-effort and may start late during busy periods; use the `Generated` timestamp in `output/latest.md` to confirm the latest completed scan.

If every configured source fails and zero jobs are collected, the workflow fails without overwriting the last good report.

## Supported public ATS URLs

- Greenhouse: `https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true`
- Lever: `https://api.lever.co/v0/postings/{site}?mode=json`
- Ashby: `https://api.ashbyhq.com/posting-api/job-board/{board}`

Only public job descriptions are processed. Verify eligibility, compensation, and availability on the employer's application page before applying.
