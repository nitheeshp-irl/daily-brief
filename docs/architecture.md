# Architecture

`daily-brief run` is a single linear pipeline. Each stage is isolated, so a failing source or LLM never stops the brief from being written.

```mermaid
flowchart TB
    CLI["CLI<br/>run · feedback · backlog · schedule"]
    SCHED["Scheduler<br/>launchd · systemd · cron"]
    CFG[("config.yaml<br/>.env")]

    subgraph SRC["1 · Collect (each source isolated)"]
        HN[Hacker News]
        RD[Reddit]
        RSS[RSS / Atom]
        GH[GitHub]
        LI["LinkedIn (opt-in)"]
    end

    subgraph PROC["2 · Process"]
        DD[Dedupe]
        HIST["History check<br/>skip recently seen"]
        FLT["Filter<br/>topics · min points"]
        SCR["Score & rank<br/>heuristics + feedback weights"]
    end

    subgraph LLMS["3 · Synthesise"]
        PRM["Prompt builder<br/>token budget · optional redaction"]
        LLM{{"LLM provider<br/>Ollama · Claude · OpenAI · Gemini"}}
        OFF["Offline digest<br/>fallback"]
    end

    subgraph VER["4 · Verify"]
        LNK["Strip URLs not in source data"]
        FIX["Repair cards<br/>refs · duplicates · empty sections"]
    end

    STORE[("SQLite store<br/>seen items · votes · backlog · cost")]
    RAW[("data/raw<br/>daily cache")]
    OUT["data/briefs/YYYY-MM-DD.md"]

    SCHED --> CLI
    CFG -.-> CLI
    CLI --> SRC
    SRC --> RAW
    SRC --> DD --> HIST --> FLT --> SCR
    HIST <--> STORE
    SCR <-. weights .-> STORE
    SCR --> PRM --> LLM
    LLM -- ok --> LNK
    LLM -. error .-> OFF --> LNK
    LNK --> FIX --> OUT
    FIX --> STORE
    OUT -. "feedback up/down" .-> STORE
```

## Stages

| Stage | What happens | Code |
|---|---|---|
| Collect | Every enabled source runs on its own. Results are cached per day in `data/raw`. | `sources/` |
| Process | Dedupe by URL, drop recently seen items, filter by topic, rank with heuristics plus your votes. | `processing/` |
| Synthesise | Build a token-budgeted prompt, call the chosen LLM, or fall back to the offline digest. | `llm/prompt.py`, `llm/providers.py` |
| Verify | Remove any URL not in the source data, then fix refs, duplicate cards and empty sections. | `llm/safety.py` |
| Save | Write the brief, then record seen items, token usage and cost. | `store.py` |

Orchestration lives in [pipeline.py](../src/daily_brief/pipeline.py).
