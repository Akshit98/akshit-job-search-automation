# Akshit Job Search Automation

A transparent, human-in-the-loop job finder tailored to Akshit Didla's resume. It collects public openings from ATS career pages, public job-board feeds, and optional aggregators; filters them using strict employment and location rules; scores resume fit; removes duplicates; and writes Markdown/CSV/JSON reports.

## Search priority

1. Full-time remote roles in India.
2. Full-time global roles that explicitly allow working from India or anywhere.
3. Full-time onsite or hybrid roles in Hyderabad.
4. Full-time onsite or hybrid roles in Bengaluru.
5. Full-time onsite or hybrid roles elsewhere in India.
6. Internships in any accepted location only when advertised compensation is explicitly at least INR 40,000 per month (or a supported equivalent).

Location-restricted remote roles outside India are excluded. The tool never applies automatically.

## Candidate basis

The profile uses Akshit's verified non-overlapping experience through April 2026: approximately 3 years and 2 months across data verification, market research, CRM data maintenance, sales support, and lead generation. The strongest evidence is:

- 5,000+ U.S. healthcare-provider records verified against official licensing sources
- duplicate resolution, record merging, missing-data research, and QA review of 100-200 record batches
- Salesforce account cleanup/enrichment, company and executive research, meeting preparation, follow-up coordination, and reporting
- U.S.-focused team support and comfort with evening/night shifts
- professional use of Excel, Google Sheets, Apollo.io, ZoomInfo, SalesIntel, LinkedIn Sales Navigator, and 6sense

HubSpot is treated as supporting experience. SQL, Power BI, Python, Microsoft 365, and AI automation are learning areas, not established professional expertise.

## Target roles

- Primary: Data Operations, Data Quality, Reference/Master Data, CRM Operations, Sales Operations, Business Operations, Operations Analyst/Associate, Market/Research Operations, provider data/credentialing, reporting, and Professional Services Operations
- Adjacent: Revenue/GTM/Commercial/Customer/Partner Operations, Sales Enablement, Salesforce support, Data Governance, Business Systems/Process Analyst, Content/Onboarding Operations, MIS/BI reporting, Operations Coordinator/Specialist, and research-led lead generation
- Pure finance, accounting, actuarial, software-engineering, product-management, quota-carrying sales, and heavily technical data roles are excluded unless the transferable fit is unusually strong

The preferred experience band is 0-4 years. Roles requiring exactly 5 years may be retained as stretches; roles requiring more than 5 years are rejected.

## Compensation quality

Employer-confirmed pay strengthens ranking. The working lower-bound targets are 6.5 LPA for remote India, 8 LPA for global remote, 7 LPA for Hyderabad, and 8 LPA for Bengaluru or other Indian cities. The corresponding targets are 8, 12, 8, 10, and 9 LPA. Because many employers do not publish pay, an otherwise strong job is retained with **Pay not disclosed** rather than silently discarded. Platform estimates never count as employer-confirmed compensation.

Listings from the past 7 days receive a freshness preference, but age is never a reason for rejection. Older vacancies remain eligible with no fixed age cutoff. Every shortlisted application page must show a live application signal and must not contain closed, expired, filled, or removed language before the job is reported.

## Quick start

1. Edit `config/profile.json` if needed.
2. Add or remove ATS company identifiers in `config/sources.json`.
3. Run:

```powershell
python -m job_search run
```

Reports are written to `output/latest.md`, `output/jobs.csv`, and `output/jobs.json`. Every run shows all currently active matches and labels first-seen jobs `NEW`. Previously seen IDs are retained in `data/seen_jobs.json`.

Before writing reports, the collector checks each shortlisted application page. HTTP 404/410 responses and explicit closed, expired, filled, removed, or no-longer-accepting messages are removed. A page must also contain a recognizable application action, such as **Apply now** or **Submit application**. Blocked or inconclusive pages are excluded from active results. There is no maximum posting age as long as hiring is verified active.

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
