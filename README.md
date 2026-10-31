# pypeit-chat

A Slack bot for **PypeIt users**: people reducing spectroscopic data with
[PypeIt](https://github.com/pypeit/PypeIt).  It answers questions such as "how
do I make a PypeIt file for my Keck/LRIS data?" or "what does this parameter
do?" from PypeIt's documentation **and** source code at the latest release.
Every answer cites its sources with links and states the PypeIt version.

The model is a public, open-weight model running locally behind an
OpenAI-compatible endpoint (Ollama by default).  Nothing leaves the host
except the Slack messages themselves.

This repository is for the bot's developers and operators.  The design is in
[`DESIGN.md`](DESIGN.md); task prompts and the work log are in `prompts/`.

## How it works

1. `pypeit_chat_ingest` keeps its own sparse, blobless clone of PypeIt in
   `cache/PypeIt`, checked out at the newest tag on `origin/release`
   (2.0.1 today).  It splits the docs into sections, `pypeit_par.rst` into
   one chunk per parameter and one per spectrograph's defaults, and the code
   into modules, classes and functions.  It links docs to code (Python roles,
   inline names, script help, spectrograph pages), reads each parameter's
   code default, uses and spectrograph overrides with `ast`, and flags places
   where docs and code differ (`index/consistency.json`).  Chunks are
   embedded with `nomic-embed-text`.
2. For a question, retrieval runs BM25 + embeddings (reciprocal rank fusion)
   over a **docs pool** and a **code pool** separately, pins exact
   identifiers (parameters, functions, scripts, spectrograph names), boosts
   the spectrograph named, and adds the code linked to the top docs hits (and
   the reverse).
3. The model gets the numbered DOCS/CODE sources, the parameters' code facts
   and any consistency flags, and is told to check the docs against the code.
   The version line and the source list are added by code, never by the
   model.
4. The Slack bot (Socket Mode: no inbound ports) streams the answer into a
   thread, with access control, rate limits, a queue, output sanitizing and a
   kill switch (DESIGN Section 11).

## Installation

On the model host (an Apple-silicon Mac; see DESIGN Section 2.3):

```console
conda create -n pypeit-chat python=3.12
conda activate pypeit-chat
pip install -r requirements.txt
pip install -e .
```

Model server (Ollama, listening on 127.0.0.1 only):

```console
ollama pull qwen3.8:27b-mlx
ollama pull nomic-embed-text
# Server settings (OpenAI-compatible API ignores per-request options):
launchctl setenv OLLAMA_CONTEXT_LENGTH 16384
launchctl setenv OLLAMA_KEEP_ALIVE 30m
```

## Build the index

```console
pypeit_chat_ingest            # clone/fetch, check out the latest tag, build index/
pypeit_chat_ingest --no-embed # keyword-only index (no model server needed)
pypeit_chat_ingest --no-update --tag 2.0.1   # options: see --help
```

A full build takes about 100 s (5,943 chunks at 2.0.1).  The index is
written atomically (`meta.json` last); a running bot reloads it without a
restart.

## Ask from the terminal

```console
pypeit_chat_ask "How do I make a PypeIt file for my Keck/LRIS red data?"
pypeit_chat_ask --sources "what is snr_thresh?"   # retrieval only
pypeit_chat_ask --slack "..."                     # the exact Slack blocks (JSON)
pypeit_chat_ask                                   # interactive, keeps history
```

## Evaluate

```console
pypeit_chat_eval --retrieval-only --calibrate   # fast: no chat model
pypeit_chat_eval                                # answers every question
pypeit_chat_eval --export review.csv            # interaction log for review
```

The questions are in `pypeit_chat/tests/eval_questions.yaml`.  Results are
written to `eval/<date>_<model>.json` so runs can be compared.

## Slack

1. Create the app from [`deploy/slack_app_manifest.yml`](deploy/slack_app_manifest.yml)
   (instructions in the file).  A workspace admin installs it.
2. `cp .env.example .env && chmod 600 .env`, then fill in the tokens,
   `HOME_TEAM_ID`, `ADMIN_CHANNEL_ID`, `ADMIN_USER_IDS` and, for the beta,
   the allowlists.
3. `pypeit_chat_slack --check` verifies the endpoint, both models, the index
   and the tokens.
4. Run `pypeit_chat_slack`, or install the launchd service
   ([`deploy/org.pypeit.chat.plist`](deploy/org.pypeit.chat.plist)) and the
   weekly index refresh
   ([`deploy/org.pypeit.chat.refresh.plist`](deploy/org.pypeit.chat.refresh.plist));
   instructions are in each file.  Logs go to `~/Library/Logs/pypeit-chat/`.

In Slack: @mention the bot in a channel (and again for follow-ups in the
thread) or DM it.  `help` explains it.  Admins can send `pause`, `resume` and
`status`; creating `cache/PAUSED` also pauses it.  👍/👎 on answers are
recorded.

## Settings

Settings come from environment variables or `.env`; DESIGN Section 9 lists
them with their defaults.  Additional settings:

| Variable | Default | Meaning |
|---|---|---|
| `THINK_SWITCH` | `reasoning_effort` | How the runtime turns reasoning on/off: `reasoning_effort` (Ollama: `"none"` is off), `chat_template_kwargs` (vLLM, llama.cpp) or `none` |
| `LLM_TIMEOUT` | `300` | Seconds to wait for the model's next token |
| `CORPUS_URL` | `https://github.com/pypeit/PypeIt.git` | Repository the corpus is cloned from |
| `HASH_SALT` | random, in `cache/hash_salt` | Salt for hashed user IDs |
| `PAUSED_FILE` | `cache/PAUSED` | Kill-switch file |

## Tests

```console
pytest
```

The unit tests use a fake PypeIt checkout, a fake OpenAI-compatible server
(`pypeit_chat/tests/mock_llm.py`) and a fake Slack client: no network, model
or Slack, about 2 s.  The mock server can also run for manual tests:

```console
python -m pypeit_chat.tests.mock_llm 11435 &
LLM_BASE_URL=http://127.0.0.1:11435/v1 CHAT_MODEL=mock pypeit_chat_ask "bspline_spacing?"
```

## Layout

- `pypeit_chat/` — the package: `config`, `llm`, `corpus`, `ingest/`,
  `index`, `answer`, `store`, `evaluate`, `ask`, `slack/`; tests in `tests/`
- `bin/` — the four commands
- `deploy/` — Slack manifest, launchd files, refresh script
- `prompts/` — task prompts and the dated work log
- `cache/`, `index/` — git-ignored: the PypeIt checkout, SQLite log, index

## Authors

- J. Xavier Prochaska
- Debora Pelliccia
- Claude
