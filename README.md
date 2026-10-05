# Akshit Job Search Automation

A transparent, human-in-the-loop job finder tailored to Akshit Didla's verified background. It collects public openings from ATS career pages, public job-board feeds, and optional aggregators; checks eligibility; assigns an automated screening score; removes duplicates; verifies application pages; and writes Markdown/CSV/JSON reports.

Verification is scheduled deterministically by application value rather than source arrival order. Public-safe retry metadata lives in `data/verification_state.json`; schema v2 contains only hashed vacancy identities and aliases, sanitized outcome classes, bounded counters, and timestamps. Aliases are established only when conservative canonical deduplication has already proved two records represent the same vacancy. Equivalent jobs rotate by scheduling age after application-value factors, and closed vacancies receive a low-priority recheck after 60 days. State writes are atomic; malformed top-level state stops the run rather than silently replacing retry history. The Markdown report shows all strong-shortlist jobs and at most 20 human-review jobs, while JSON and CSV retain the complete review and verification backlogs. State pruning remains a deferred maintenance enhancement because expected one-year growth is manageable.

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

`screening_score` remains an automated prioritization signal based on title/function relevance, verified candidate-skill overlap, domain compatibility, seniority, evidence quality, experience, and compensation. Exclusion rules operate separately and always override the score. It does not determine Actual Hiring Fit.

Actual Hiring Fit is assessed separately from structured, provenance-bearing evidence in `config/evidence.json`. The six explicit categories are direct responsibilities (35), tools/domain (25), years/seniority/leadership (15), transferable evidence (15), education/certifications (5), and practical requirements (5). Requirement extraction retains unmatched and unclassified material requirements, and category credit reflects breadth and centrality rather than a small recognized keyword subset. Reports retain requirement-coverage confidence, the raw sum, every applicable mandatory-gap cap, and the final capped score. When coverage or source evidence is insufficient, Actual Hiring Fit remains null. ATS/Resume Similarity is weighted requirement coverage and remains separate from Actual Hiring Fit and career value.

## Quick start

1. Edit `config/profile.json` if needed.
2. Add or remove ATS company identifiers in `config/sources.json`.
3. Run:

```powershell
python -m job_search run
```

Use `python -m job_search run --dry-run` to collect and print a diagnostic without changing tracked reports or `data/seen_jobs.json`.

Private application status is stored locally and is never loaded by scheduled report generation. Update it with:

```powershell
python -m job_search status JOB_ID applied --note "Submitted directly"
```

Supported states are `new`, `reviewed`, `saved`, `applied`, `interview`, `offer`, `rejected`, `closed`, and `not_pursuing`. The default file is ignored at `private/application_state.json`; set `JOB_SEARCH_PRIVATE_STATE` to use a different private path. `config/application-state.example.json` documents the public schema without personal data. Public suppression/fingerprints and private status history intentionally remain separate layers.

Reports are written to `output/latest.md`, `output/jobs.csv`, and `output/jobs.json`. Every run shows all currently active matches and labels first-seen jobs `NEW`. Previously seen IDs are retained in `data/seen_jobs.json`. Seen detection accepts stable source IDs, legacy deployed fingerprints, and versioned current fingerprints; when a direct-employer record replaces a seen aggregator copy, the canonical representative inherits the group's seen status.

Before writing reports, the collector checks each non-suppressed application page. HTTP 404/410 responses and explicit closed, expired, filled, removed, or no-longer-accepting messages are removed. For aggregator and public-board results, the collector follows ordinary public apply links when available and verifies the resolved employer/application destination; it does not treat an accessible aggregator shell as proof that the employer vacancy is active. Blocked, inconclusive, or evidence-thin pages remain in the review queue. Verification outcomes are published only as sanitized classes such as `verified_active`, `http_blocked`, `timeout`, `aggregator_page_only`, or `redirect_to_generic_index`; raw exceptions and credential-bearing URLs are never written to public artifacts.

## Application-state privacy

Application status, notes, and personal history stay in the ignored private local state. Scheduled public automation does not read this file, and public reports and Slack notifications do not include it. The existing non-sensitive seen-ID/fingerprint layer remains the only repository-visible suppression mechanism; broader disposition-aware public suppression is deliberately deferred.

## Sources

- ATS: Greenhouse, Lever, Ashby
- Public job boards: Remote OK, Remotive, Arbeitnow, Himalayas, The Muse
- Optional aggregators: Adzuna and Jooble
- Private companion automation: Gmail alerts from LinkedIn, Naukri, Indeed, Foundit, Glassdoor, Wellfound, and Instahyre

Gmail alert links are intentionally never written to this public repository because some contain personalized authentication or tracking tokens.

Source definitions and career-target queries are centralized in `config/sources.json`. Direct employer ATS boards carry canonical employer names and higher source priority than public boards and aggregators. Every collected job records its source type, board identifier where applicable, description provenance, original source URL, resolved final URL, canonical employer URL where available, and retrieval status. Duplicate clustering uses canonical URLs, employer ATS identity, normalized employer/title, and strong content similarity; fingerprint equality only finds candidates and never authorizes a merge. Direct employer versions win while distinct employer requisitions remain separate. Per-source health is stage-aware across collection, eligibility, canonical deduplication, verification, and final queues.

Canonical page verification accepts only HTTP(S), rejects embedded credentials and non-public destinations, validates every resolved A/AAAA address, pins the connection to a validated address, and explicitly validates each redirect. A run submits at most 100 jobs, attempts at most 300 HTTP requests, follows at most five redirects per fetch, uses a 12-second request timeout, and reads at most 2 MB per response. Jobs beyond the budget remain in review with a sanitized `verification_budget_exhausted` outcome; they are never silently dropped or promoted to the strong shortlist.

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
