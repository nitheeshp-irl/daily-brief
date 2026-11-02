# daily-brief

A local, privacy-first **daily briefing agent**. Every morning it pulls from the sources you
pick (Hacker News, Reddit, RSS, GitHub, and optionally your own LinkedIn session), throws away the
noise, ranks what's left, and has an LLM (local Ollama by default) write a short Markdown brief
with links back to every item.

```
sources ──► dedupe ──► filter ──► score (+ your feedback) ──► LLM ──► verify links ──► brief.md
 HN · Reddit · RSS · GitHub · LinkedIn*   history   topics   heuristics          no hallucinated URLs
                                                        * opt-in, read docs/linkedin.md first
```

- **Runs locally.** Your data stays in `./data` (SQLite + JSON). Ollama keeps summarisation on your machine.
- **Works without any account.** Try it now, with no network, no keys, no LLM: `daily-brief run --sample --model none`.
- **Learns what you like.** Rate items 👍/👎 and the ranking adapts.
- **Reading-list triage.** Tags, ages and flags stale items so your "saved" pile becomes useful.
- **Knows what it costs.** Token and dollar report after every run.

## What a brief looks like

Real output from public sources only (Hacker News, RSS, GitHub), written by a small local model
(`gemma4:e2b`, about 40 seconds, free). Every heading links to the item; every card ends in a `ref`
you can vote on. Full file: [examples/real_brief_gemma4.md](examples/real_brief_gemma4.md).

```markdown
### [Show HN: OmniDiff – Opensource, super fast, content aware Git difftool, in Rust](https://github.com/ivankovic/omnidiff) · hn
- **What it is:** An open-source tool written in Rust that performs fast diffs across code, config and images.
- **Key points:**
  - Can diff code from 25 languages.
  - Can diff C++ files of tens of thousands of lines in under 100ms.
- **Advantages:** Extremely fast performance for complex diff operations.
- **Watch out:** It is currently in v0.3.0 and still under development.
- **Why it matters to you:** Provides a high-performance diffing utility for code and configuration files. (ref: fac8013)
```

Not an AWS/DevOps person? Start from [examples/config.general.yaml](examples/config.general.yaml)
(AI, programming, security and open-source topics, plus a few developer blogs) and edit from there.

## Quick start

```bash
git clone <this repo> && cd daily-brief
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                     # core: HN, Reddit, RSS, GitHub + Ollama
# extras: pip install -e ".[claude]"  ".[openai]"  ".[gemini]"  ".[linkedin]"  ".[all]"

daily-brief run --sample --model none   # 1. see it work (bundled fake data, nothing saved)
daily-brief init                        # 2. write a starter config.yaml, then edit it
daily-brief doctor                      # 3. check your setup
ollama pull gemma4:e2b                 #    (local LLM; or use --model claude / gpt4o-mini / gemini)
daily-brief run                         # 4. real run → data/briefs/YYYY-MM-DD.md
daily-brief schedule install --time 08:00   # 5. every morning
```

If the LLM is down, you still get a brief: `llm.fallback_offline` writes a deterministic digest.

## Commands

| Command | What it does |
|---|---|
| `run` | Collect → process → brief. Flags: `--model`, `--dry-run`, `--estimate`, `--refetch`, `--force`, `--sample`, `--redact`, `--only hackernews,rss`, `--no-history` |
| `doctor [--online]` | Checks Python, config, Ollama/API keys, optional deps, LinkedIn session |
| `init` | Writes a starter `config.yaml` |
| `feedback up\|down <ref…>` | Vote on items from the brief's **Sources index**; `list`, `weights`, `reset` |
| `backlog list\|stale\|summary\|read\|archive\|tag\|add` | Reading-list triage |
| `schedule install\|uninstall\|status\|run-now` | launchd (macOS) / systemd user timer (Linux) / cron line |
| `cost` | Token and cost history |
| `linkedin login\|status` | Session helpers for the opt-in LinkedIn source |

## Sources

| Source | Needs | Notes |
|---|---|---|
| `hackernews` | nothing | Front page via the public Algolia API; `min_points` filter |
| `reddit` | nothing | Top-of-day via public Atom feeds. Reddit throttles anonymous clients, so some subreddits can fail; the others still work |
| `rss` | nothing | Any RSS 2.0 / Atom feed; parsed with `defusedxml` |
| `github_trending` | optional `GITHUB_TOKEN` | New repos gaining stars (official search API; GitHub has no official "trending" API) |
| `linkedin` | **opt-in** | Your own logged-in session. **Read [docs/linkedin.md](docs/linkedin.md) before enabling.** |

Each source is isolated: if one fails, the brief is still written and the footer says which one failed and why.

## LLMs and cost

`--model` / `llm.model`:

| Spec | Needs |
|---|---|
| `none` | nothing: deterministic digest |
| `ollama` or `ollama:<model>` | [Ollama](https://ollama.com) running |
| `claude`, `claude-sonnet`, `claude-opus`, `claude:<model>` | `ANTHROPIC_API_KEY`, `pip install ".[claude]"` |
| `gpt4o`, `gpt4o-mini`, `openai:<model>` | `OPENAI_API_KEY`, `pip install ".[openai]"` |
| `gemini`, `gemini-pro`, `gemini:<model>` | `GOOGLE_API_KEY`, `pip install ".[gemini]"` |

Shorthands map to model IDs in `llm.models` and **model names go stale**, so edit them freely. The
Ollama, Anthropic, OpenAI and Gemini adapters share one interface; Ollama is the path exercised
end-to-end in the maintainer's setup, and the cloud adapters are covered by mocks only. Reports of
breakage are welcome.

**Small local models differ a lot.** Tested by the maintainer on one laptop with this pipeline:

| Model | Result |
|---|---|
| `gemma4:e2b` (default) | Follows the card template well: what it is, key points, advantages, caveats. Takes about 40 to 90 seconds. Still slips occasionally, e.g. repeats an item under several sections; the pipeline repairs that. |
| `qwen2:7b` | Often ignored the template, dropped links and invented details. Not recommended. |
| Claude, OpenAI, Gemini | Adapters are written and unit-tested with mocks, but not yet run live. Reports welcome. |

Quality is mostly the model's, but the parts that must be right never depend on it. After the model
answers, the pipeline strips any URL not in your data, rebuilds every card heading as
`[title](link) · source` from the item itself, removes duplicate cards, empty sections and echoed
prompt text, and fixes wrong `ref`s. Use `--model none` for a no-LLM digest.

After each run you get e.g. `ollama:gemma4:e2b · 14,689 in / 5,490 out tokens · free (local)`.
`daily-brief run --estimate` shows the input size and an upper-bound cost before spending anything.
Built-in prices cover only a few well-known models and can be out of date; set `llm.pricing`
(USD per 1M tokens) for yours. Unknown models report tokens and **no invented dollar figure**.

### Privacy when using a cloud LLM
Posts (and, unless `--redact`, your `profile` block) are sent to the provider you choose.
`--redact` / `llm.redact: true` swaps author names for pseudonyms, hides your profile and scrubs
emails/phone numbers; it is best-effort, not anonymisation. Prefer Ollama for anything sensitive.

### Prompt-injection hardening
Scraped posts are untrusted. They are wrapped in `<untrusted_data>` with a system prompt telling the
model to treat them as data only, and **every URL in the model's output is checked against the URLs in
the source data**; unknown ones are stripped and counted in the footer.

## Feedback loop

Each brief ends with a **Sources index** of short refs. Vote on what was useful:

```bash
daily-brief feedback up   a1b2c3d 9f8e7d6
daily-brief feedback down 55aa66b
daily-brief feedback weights      # what it has learned (origin, author, domain, topic, keyword)
```

Votes nudge per-feature weights (capped), so the same authors, domains and topics rank higher or lower
tomorrow, and the "why it ranked" reasons mention your feedback. `feedback reset` clears it.

## Reading-list triage

LinkedIn saved posts (when enabled) and anything you `daily-brief backlog add <ref>` land in a backlog with
auto-tags from your topic groups. The brief shows how many are unread/stale and the oldest few.

```bash
daily-brief backlog summary
daily-brief backlog stale            # unread for ≥ triage.stale_days (default 30)
daily-brief backlog list --tag aws
daily-brief backlog read a1b2c3d     # or: archive / unread / tag <ref> <tag…>
```

Age is measured from when the tool first saw an item, so staleness builds up over time.

## Configuration

`daily-brief init` writes [config.yaml](src/daily_brief/config.example.yaml); anything you omit uses
the built-in default. Key sections: `profile`, `topics` (groups → allow-list, sections, tags), `filters`,
`sources`, `history`, `triage`, `llm`. Secrets go in `.env` (see `.env.example`), never in YAML.

Config search order: `--config`, `$DAILY_BRIEF_CONFIG`, `./config.yaml`, `~/.config/daily-brief/config.yaml`.
Data and logs paths are relative to the config file.

## Scheduling

`daily-brief schedule install --time 08:00` creates a launchd agent (macOS) or systemd user timer (Linux), or
prints a cron line. It runs with the interpreter you installed into, and carries over your `PATH` so `uvx` and
`ollama` resolve. launchd/systemd run missed jobs when the machine wakes, and `run` is idempotent per day
(`--force` to regenerate), so catch-ups never duplicate.

## Development

```bash
pip install -e ".[dev]"
ruff check . && pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Layout:

```
src/daily_brief/
  sources/        hackernews · reddit · rss · github · linkedin/
  processing/     filter (config-driven) · scoring (heuristics + feedback weights)
  llm/            providers · prompt (token-budgeted) · safety · cost · offline digest
  pipeline.py     orchestration        store.py  SQLite     triage.py / feedback.py
  schedule.py · doctor.py · cli.py
tests/            fixtures only; no network, no accounts
```

## License

MIT, see [LICENSE](LICENSE). Not affiliated with LinkedIn, Reddit, Y Combinator, GitHub, or any LLM provider.
