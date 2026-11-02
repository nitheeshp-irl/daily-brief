# LinkedIn source (opt-in)

**Read this before enabling `sources.linkedin`.**

## What it does
It reads **your own** LinkedIn data using **your own logged-in browser session**: your home feed and topic
searches through a third-party MCP server (`mcp-server-linkedin`), and your saved posts through a headless
browser (`patchright`, a Playwright fork). Nothing is posted, liked or sent.

## The risk
LinkedIn's User Agreement prohibits automated scraping and bots. Using this can lead to LinkedIn restricting or
closing your account. It is your account and your decision. This project does not make the risk go away, and
it is **not** designed or advertised to evade detection. The measures it takes are about being a polite client:
pauses between requests, a small number of pages, and a logged-in session you created yourself.

Because of that:

- It is **off by default** and refuses to run unless you set `sources.linkedin.accept_tos: true`.
- Use it for **personal use only**. Don't run it for other people's accounts, don't resell the output, and
  don't republish scraped posts.
- Everything else in `daily-brief` works without it. If you want zero LinkedIn risk, leave it disabled
  (the default) and use the other sources.
- Posts are other people's content; don't commit `data/raw/` anywhere public (it's gitignored).

## Setup
```bash
pip install -e ".[linkedin]"        # aiohttp + patchright
daily-brief linkedin login          # opens a browser; log in normally, then close it
daily-brief linkedin status
# config.yaml → sources.linkedin: {enabled: true, accept_tos: true}
daily-brief doctor
```
You need [`uv`](https://docs.astral.sh/uv/) (`uvx`) installed and a Chromium that patchright can launch.

## Safety notes
- **The MCP server version is pinned** (`sources.linkedin.mcp_version`, default tested: `4.29.0`). That package
  runs with your logged-in session; letting `@latest` auto-update means trusting every future release.
  Upgrade deliberately.
- The server listens on a **random free localhost port**, and nothing else on your machine is killed to free it.
- The MCP server and the saved-posts browser both use `~/.linkedin-mcp/profile`, so they run one after the other,
  never together.

## Troubleshooting
| Symptom | Likely cause |
|---|---|
| `LinkedIn session expired` | Run `daily-brief linkedin login` again |
| `Loaded … but parsed 0 items; markup likely changed` | LinkedIn changed its page. The saved-posts parser reads page text and is brittle by nature. Please open an issue with the (redacted) text |
| `MCP server exited early` | Run `uvx mcp-server-linkedin==<version> --status` by hand to see the error |
| Everything fails after an update | Pin back to a version you know works |
