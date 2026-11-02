# Security

Please report vulnerabilities privately (GitHub "Report a vulnerability" / security advisory) rather than in a public issue.

Things this project treats as security-relevant:

- Secrets: API keys live only in `.env`/environment, never in YAML or logs.
- Prompt injection: scraped content is untrusted; output links are verified against the source data.
- XML: feeds are parsed with `defusedxml`.
- Local state: the SQLite DB and raw JSON may contain other people's posts. They are gitignored; don't publish them.
- The LinkedIn source runs a third-party package with your logged-in session; it is pinned and opt-in (docs/linkedin.md).
