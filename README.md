# Akshit Job Search Automation

A transparent, human-in-the-loop job finder tailored to Akshit Didla's resume. It collects public openings from Greenhouse, Lever, and Ashby, filters them using strict employment and location rules, scores resume fit, removes duplicates, and writes Markdown/CSV/JSON reports.

## Search priority

1. Full-time remote roles in India.
2. Full-time global roles that explicitly allow working from India or anywhere.
3. Full-time onsite or hybrid roles in Hyderabad.
4. Full-time onsite or hybrid roles in Bengaluru.
5. Full-time onsite or hybrid roles elsewhere in India.
6. Internships in any accepted location only when advertised compensation is explicitly at least INR 40,000 per month (or a supported equivalent).

Location-restricted remote roles outside India are excluded. The tool never applies automatically.

## Target roles

- CRM / Salesforce operations and data quality
- Business, sales, revenue, and research operations
- Market research and research analyst
- Lead generation and data operations
- Healthcare/provider data verification and quality assurance

## Quick start

1. Edit `config/profile.json` if needed.
2. Add or remove ATS company identifiers in `config/sources.json`.
3. Run:

```powershell
python -m job_search run
```

Reports are written to `output/latest.md`, `output/jobs.csv`, and `output/jobs.json`. Previously seen job IDs are retained in `data/seen_jobs.json`.

Run the tests with:

```powershell
python -m unittest discover -s tests -v
```

## GitHub Actions

The included workflow runs every weekday at 09:00 IST (03:30 UTC) and can also be started manually. It commits updated reports and deduplication state back to the repository. GitHub cron may start a few minutes late during busy periods.

## Supported public ATS URLs

- Greenhouse: `https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true`
- Lever: `https://api.lever.co/v0/postings/{site}?mode=json`
- Ashby: `https://api.ashbyhq.com/posting-api/job-board/{board}`

Only public job descriptions are processed. Verify eligibility, compensation, and availability on the employer's application page before applying.
