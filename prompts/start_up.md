# Getting started

## Goals

This repository will create and operate a chatbot for PypeIt developers that
answers questions grounded in PypeIt's source code and documentation.  The aim
is a helper that developers can ask "where is X done?", "how do I add a new
spectrograph?" or "what does this parameter do?" and get an answer that cites
the relevant code or docs, rather than a guess.  It will run on a public,
open-weight model hosted locally on a Mac Studio.

## Prompts

1. Read this file.  Execute the 1st task under "Claude/CLAUDE.md file"
2. Read this file.  Execute the 1st task under "Claude/Skills"
3. Read this file.  Execute the 1st task under "Claude/Settings"
4. Read this file.  Execute the 1st task under "Basic start up"
5. Read this file.  Execute the 1st task under "Context"

## Claude

### CLAUDE.md file

1. Examine all of the current files in the repository.  Then generate a basic
   CLAUDE.md file for this project.  Have it indicate:

    - I will perform git commands.  Read-only git (status, diff, log, show,
      branch) is fine; anything that changes repository state is mine to run.
    - If you do any calculation, generate it as a python script and write it to
      disk so that I can add it to the Repository.
    - If you need to run Python, use the "pypeit14" conda environment, invoked
      as `conda run -n pypeit14 python ...`.  Never the system Python.
    - The PypeIt source lives at `/Users/xavier/Projects/PypeIt/PypeIt` and the
      development suite at
      `/Users/xavier/Projects/PypeIt/PypeIt-development-suite`.  The Sphinx
      docs (`.rst`) are in `PypeIt/doc/` and are published at
      https://pypeit.readthedocs.io.  These are the chatbot's source material:
      read from them freely, never write to them.
    - Prompt docs live in `prompts/` (not `claude_prompts/`).  Read the
      relevant one before acting, and do only the numbered task you were
      pointed at.
    - Log your work in the Logs section of the relevant prompt doc.

### Skills

1. Copy over the skills/ files from the IOPtics repository
   (`/Users/xavier/Oceanography/python/IOPtics/.claude/skills`) into
   `.claude/skills/` here.  That is `critical-partner` and `grill-me`.

### Settings

1. Generate a `.claude/settings.json` for the project that allows you to run
   most bash commands and run Python (use the "pypeit14" conda environment).
   Start from `/Users/xavier/Projects/PypeIt/first-hires-exoplanet/.claude/settings.json`,
   which is already the IOPtics policy pruned and switched to `pypeit14`.
   Copy the policy, not any path-specific allow entries.  Keep the deny list,
   in particular `git push` / `git commit` / `git reset` / `git rebase`, so the
   permission system enforces the git rule above.  For WebFetch, keep the
   PypeIt docs and add `github.com` / `api.github.com` (PypeIt issues and
   discussions) and the Claude API docs; drop KOA and the `pypeit_setup` /
   `run_pypeit` entries, since this repo does not reduce data.

## Basic start up

1. Generate the basic files that one needs for a Python GitHub repository, e.g.
   a file for dependencies.  Examine the other Repositories in
   `Projects/PypeIt` to see how I tend to organize things (`setup.py` +
   `requirements.txt` + `pytest.ini`, not `pyproject.toml`).  The package
   directory must be `pypeit_chat` — the repo name has a hyphen, which is
   invalid in a module name.  The `.gitignore` already exists; append to it
   rather than replace it, and make sure `.claude/settings.local.json` and any
   file holding API keys (e.g. `.env`) are ignored.  Log your work in the Logs
   section.

## Context

1. Please explore and write a Report below.  Do no editing yet.  Cover:

    - What the chatbot has to draw on: the size and structure of
      `PypeIt/doc/` (rst pages, auto-generated API pages, parameter tables,
      spectrograph pages), the docstrings in `PypeIt/pypeit/`, and anything
      else worth indexing (dev-suite PypeIt files, GitHub issues/discussions).
      Rough token counts would help.
    - The main design options for grounding answers — e.g. retrieval over a
      chunked index vs. loading large slices of the docs into context, and
      whether code should be searched on demand as a tool — with your
      recommendation and its weaknesses.
    - Where it might run (local CLI, a web page, Slack, a docs-site widget)
      and what each implies for hosting and cost.
    - How to keep the index in sync as PypeIt and its docs change.
    - How we would evaluate it: a starter set of real user questions with
      known good answers.

   End with a short list of questions for me, each with your recommended
   answer.

### Report

Surveyed against PypeIt `0fd778f07` (2026-10-16).  Sizes come from
`pypeit_chat/survey_sources.py`; tokens are estimated as characters / 4.

**1. What the chatbot can draw on**

| Source | files | ~tokens | Notes |
|---|---:|---:|---|
| `doc/pypeit_par.rst` | 1 | 209k | Auto-generated: every ParSet table plus default parameters for 87 spectrographs.  Mostly tables, so it suits lookup, not prose retrieval |
| `doc/include/` | 86 | 104k | Generated tables (datamodels, spectrograph table, calspec) pulled in by other pages |
| `doc/` top level | 46 | 94k | The core user guide: `setup`, `running`, `pypeit_file`, `cookbook`, `reduction_tips`, `trouble`, `coadd*`, `fluxing`, `out_*` |
| `doc/tutorials/` | 18 | 90k | Instrument how-tos (DEIMOS, LRIS, MOSFIRE, HIRES, NIRES, ...).  Highest-value prose |
| `doc/spectrographs/` | 32 | 51k | Per-instrument notes |
| `doc/help/` | 60 | 43k | `--help` output of each `pypeit_*` script |
| `doc/dev/` | 22 | 42k | Developer docs |
| `doc/calibrations/` | 22 | 35k | Bias, flats, slits, arcs, wavelength calibration, tilts, flexure |
| `doc/releases/` | 26 | 18k | Release notes; useful for "what changed?" questions |
| `doc/api/` | 273 | 15k | `automodule` stubs only.  The actual API text is the docstrings |
| **`doc/` total** | | **712k** | ~500k without `pypeit_par.rst` |
| `pypeit/*.py` (all) | 358 | 2.1M | |
| `pypeit/*.py` docstrings only | | 620k | |
| dev-suite `*.pypeit` | 288 | 220k | Worked PypeIt files for each instrument/setup.  Good answers to "what does a working file for X look like?" |

Outside the repos:

- **GitHub issues:** about 810, open and closed.  GitHub Discussions is
  turned off, and only 5 issues have the `question` label, so user Q&A does
  not happen on GitHub.
- **Slack:** the docs send users to the PypeIt Users Slack
  (`pypeit-users.slack.com`), which is where the real questions are.  It is
  the best source of evaluation questions and could also be indexed, if the
  workspace's privacy expectations allow it.

**2. Grounding design — recommendation: an agent with search tools, not one big context**

- *Put everything in the context window.*  The docs alone are ~0.7M tokens.
  Even if that fits a large-context model, every question would pay for all
  of it.  Rejected.
- *Classic retrieval (chunk + embed + top-k).*  It is the standard approach
  and cheap per query.  But PypeIt questions often need an exact identifier
  (a parameter name, a spectrograph name, a script flag), and embeddings
  handle those badly.  It also needs an embedding provider and a vector
  store to run and keep in sync.
- *Recommended: Claude with a small set of tools over a local, pinned
  checkout of PypeIt and the dev suite.*
  - `search_docs`: keyword/BM25 search over `.rst` split into sections,
    returning the title, path and readthedocs URL of each hit
  - `read_doc`: return one section
  - `lookup_param`: look up a ParSet keyword (default, type, description),
    and optionally a spectrograph's override, by importing `pypeit.par` and
    calling `Spectrograph.default_pypeit_par()` rather than parsing the
    209k-token rst
  - `grep_code` / `read_code`: search and read the source, for questions
    the docs do not answer
  - `example_pypeit_file`: return a dev-suite PypeIt file for an instrument

  A short system prompt plus ~20–40k tokens of core pages (index, cookbook,
  setup, running, pypeit_file) would be sent with every request, using prompt
  caching.  Answers must cite paths or URLs.

  *Weaknesses:* several tool calls per question, so it is slower and costs
  more per answer than single-shot retrieval.  Keyword search misses
  paraphrases (e.g. "my sky lines look bad" vs. `skysub.rst`); embeddings can
  be added as a second search mode later if the evaluation shows that
  matters.  `lookup_param` needs PypeIt importable on the server.

- A trap worth knowing: `snr_thresh` exists in three ParSets with defaults
  1.0, 5.0 and 10.0 (`FindObjPar` is the 10.0).  An ungrounded model will
  happily pick one; the tools have to return the ParSet name with the value.

**3. Where it could run**

- *Local CLI* (`pypeit_chat` script): the fastest to build and test; uses
  your API key.  This is the prototype.
- *Slack bot in PypeIt Users Slack*: it goes where the questions already are.
  It needs an always-running process (a small VM or a serverless function
  plus Slack's Events API), Slack app approval from the workspace admins,
  and a usage cap, since every question costs tokens on the team's key.
- *Widget on the readthedocs site*: the most visible option, but it needs a
  public backend holding the API key, so it needs rate limiting and abuse
  protection.  Highest operational burden.
- *Standalone web page*: the same backend issues as the widget, with less
  reach.

Cost per answer is roughly 30–60k input tokens (mostly cached) plus 1–2k
output tokens.  Check current API pricing before choosing the model tier.

**4. Keeping the sources in sync**

The sources are a git checkout pinned to a PypeIt release tag (or
`release`/`develop`).  Re-indexing means `git pull` plus a rebuild of the
section index.  It could run in CI on each PypeIt release, or nightly, and
record the commit, so answers can say "as of PypeIt 2.x".  Users often run
older versions; readthedocs keeps versioned docs, so the bot could ask for
the user's version and link to the matching docs.

**5. Evaluation**

Build `pypeit_chat/tests/eval_questions.yaml`: question, expected source(s),
key facts that must appear.  Grade with a script, using string checks for
facts and an LLM judge for the rest.  Starter set, with where each answer
lives:

1. "How do I make a PypeIt file for my Keck/LRIS red data?" → `setup.rst`, `tutorials/lris_howto.rst`
2. "PypeIt ran but no 1D spectra were extracted. Why?" → `reduction_tips.rst` ("No 1D Spectra Extracted")
3. "What is the default S/N threshold for object finding and how do I lower it?" → `FindObjPar.snr_thresh` = 10.0, set in `[reduce][[findobj]]`
4. "How do I coadd 1D spectra from several exposures?" → `coadd1d.rst`
5. "How do I flux-calibrate my spectra?" → `fluxing.rst`
6. "Things broke after I upgraded PypeIt." → `trouble.rst`
7. "Is the SOAR TripleSpec supported?" → `include/spectrographs_table.rst`, `tutorials/soar_triplespec_howto.rst`
8. "How do I read a spec1d file in Python?" → `out_spec1D.rst`
9. "How do I do A-B sky subtraction for my NIR data?" → `A-B_differencing.rst`
10. "My wavelength solution failed. What can I do?" → `calibrations/wave_calib.rst`

To grow this to ~50–100 questions, take real questions from Slack and from
closed GitHub issues.  Hand-written questions alone will be too easy.

**Questions for you (my recommended answer in brackets)**

1. Who is the primary audience: users reducing data, or developers working
   on the code?  [Users first.  Developer questions come almost for free
   once the code tools exist.]
>A. This Repo is for developers, not users
2. Where should v1 live?  [Local CLI to evaluate, then a Slack bot in
   PypeIt Users Slack.  Not the readthedocs widget for now.]
>A. TBD
3. Is indexing the Users Slack history acceptable, even just to build the
   evaluation set?  [Use it for evaluation questions only; do not index or
   quote it.]
>A. TBD
4. Which PypeIt version should it answer for?  [The latest release tag, and
   say so in every answer.]
>A. Latest release tag, and say so in every answer.
5. Whose API key pays, and what is the budget?  [A dedicated PypeIt
   Anthropic key with a monthly spend cap.]
>A. We will be running a public, open-weight model on my Mac Studio
6. Retrieval approach?  [Tool-based keyword search over a pinned checkout
   (section 2); add embeddings only if the evaluation shows paraphrase
   misses.]
>A. TBD

**Decisions (2026-10-28), and what they change in this Report**

- *Audience: developers, not users.*  The center of gravity moves from the
  user guide to the code.  The main sources become `pypeit/` (the code and
  its ~620k tokens of docstrings), `doc/dev/` (22 pages, including
  `new_spectrograph.rst`, `new_script.rst` and `development.rst`),
  `doc/dev_suite.rst`, and the GitHub history: ~810 issues and ~1420 PRs.
  The PRs are where design decisions are explained.  `grep_code` /
  `read_code` become the primary tools, not the fallback.  A `git log` /
  `git blame` tool over the checkout would answer "why is it done this way?"
  questions.  The user-facing tutorials still matter as context, but less.
  The user evaluation set in section 5 is replaced by the developer set
  below.
- *Model: a public open-weight model on the Mac Studio.*  There is no
  per-token cost, so section 3's cost estimate no longer applies.  The
  constraints become:
  - Unified memory sets the largest model that fits.
  - Long prompts are slow to process on Apple silicon, so the context should
    stay small.  That makes the "everything in context" option even less
    workable, and good retrieval more important.
  - Open-weight models vary a lot in how reliably they call tools, and the
    recommended design in section 2 depends on that.  This is the main new
    risk.

  Recommendation: serve the model behind an OpenAI-compatible local endpoint
  (MLX `mlx_lm.server`, llama.cpp `llama-server`, Ollama and LM Studio all
  provide one).  Write `pypeit_chat` against that interface, so that trying a
  different model or runtime is a configuration change, not a code change.
  The `anthropic` dependency has been removed.
- *Version: the latest release tag, stated in every answer.*  That is
  **2.0.1** today.  ⚠ Two consequences:
  - The sibling checkout at `/Users/xavier/Projects/PypeIt/PypeIt` is on the
    feature branch `edge_2208`, 1374 commits past 2.0.1.  The bot therefore
    needs its own checkout or worktree at the tag (for example under the
    git-ignored `cache/`), never your working copy.
  - Developers work on `develop`, so a bot pinned to 2.0.1 will sometimes
    describe code that has already changed.  That may be what you want for
    stability.  If not, `develop` with its commit hash in each answer is the
    alternative.  Worth revisiting after the first evaluation round.
- *Where v1 runs (TBD), Slack history (TBD), retrieval approach (TBD).*  The
  open-weight decision favors settling retrieval empirically.  Run the same
  evaluation set through (a) the tool-based agent and (b) single-shot hybrid
  retrieval (keyword search plus a local embedding model, so nothing leaves
  the Mac Studio).  Compare accuracy and latency on the chosen model.  For a
  developer audience, the closed GitHub issues and PRs are a better source of
  real evaluation questions than the Users Slack.

**Developer evaluation starter set** (replaces the one in section 5)

1. "How do I add support for a new spectrograph?" → `doc/dev/new_spectrograph.rst`, `pypeit/spectrographs/spectrograph.py`
2. "How do I add a new `pypeit_*` script?" → `doc/dev/new_script.rst`, `pypeit/scripts/`
3. "How do I add a new parameter to a ParSet?" → `pypeit/par/pypeitpar.py` (defaults/dtypes/descr pattern)
4. "Which spectrographs override `FindObjPar.snr_thresh`, and to what?" → `grep` over `pypeit/spectrographs/*.py` (10 files mention `snr_thresh`)
5. "Where is global sky subtraction implemented?" → `pypeit/core/skysub.py`
6. "How does a `DataContainer` define and write its datamodel?" → `pypeit/datamodel.py`
7. "Where are the image bitmask flags defined?" → `pypeit/images/imagebitmask.py`, `doc/bitmasks.rst`
8. "How does automatic wavelength identification work?" → `pypeit/core/wavecal/autoid.py`, `pypeit/wavecalib.py`
9. "How do I run the dev-suite tests for one instrument?" → `doc/dev_suite.rst`
10. "How is the instrument configuration decided for HIRES?" → `doc/dev/hiresconfig.rst`

## Logging

The "Logs" section will record Claude's work.  Please use the following format:

### <Date> (Short summary of the work)

<Detailed description of the work and what you learned>

...

## Logs

### 2026-10-28 (Generated CLAUDE.md)

Executed task 1 under "Claude/CLAUDE.md file".  Examined every file in the
repository: `README.md` (two lines), `LICENSE` (BSD 3-Clause, "Copyright (c)
2026, PypeIt"), `.gitignore` (the stock GitHub Python template, 220 lines) and
`prompts/start_up.md`.  Two commits (`e443b00 Initial commit`, `7a5aa97 go`).
Created `CLAUDE.md` in the shape of first-hires-exoplanet's: Project, Workflow
conventions (git is the user's, read-only git allowed; calculations as
scripts; `prompts/` not `claude_prompts/`; log under `## Logs`), Environment
(`pypeit14` via `conda run -n pypeit14`), and Related repositories (PypeIt
source, `doc/` and readthedocs, dev suite), described as read-only source
material for the chatbot.  What I learned: `pypeit14` is the conda env on
this machine (`/Users/xavier/miniforge3/envs/pypeit14`).  The PypeIt checkout
has a `doc/claude/` directory (a Shane Kast blue analysis), so Claude-era work
is already landing in the PypeIt docs tree.  Only read-only git (`log`,
`show`) was run.

### 2026-10-28 (Copied critical-partner and grill-me skills)

Executed task 1 under "Claude/Skills".  Created `.claude/skills/` and copied
`critical-partner/SKILL.md` (2629 bytes) and `grill-me/SKILL.md` (635 bytes)
from `/Users/xavier/Oceanography/python/IOPtics/.claude/skills`; `diff -r`
against the source is clean.  Both are repo-agnostic, so they needed no
changes.  No git commands were run.

### 2026-10-28 (Created .claude/settings.json)

Executed task 1 under "Claude/Settings".  Derived `.claude/settings.json` from
`first-hires-exoplanet/.claude/settings.json`, which is already the pruned
IOPtics policy on `pypeit14`.  Removed `Bash(pypeit_setup:*)`,
`Bash(run_pypeit:*)` and `WebFetch(domain:koa.ipac.caltech.edu)` (this repo
does not reduce data).  Added WebFetch for `github.com`, `api.github.com`,
`raw.githubusercontent.com`, `docs.claude.com` and `platform.claude.com`.
Kept `pypeit.readthedocs.io` and the literature domains (doi, ADS, arXiv,
IOP).  Deny list (`sudo`, `rm -rf /`, `rm -rf ~`, `git push`/`commit`/
`reset`/`rebase`) and `ask` on `rm` are unchanged.  The result is 44 allow,
7 deny and 1 ask entries, and it parses as JSON.  No git commands were run.

### 2026-10-28 (Basic Python repo files)

Executed task 1 under "Basic start up", using first-hires-exoplanet as the
template (the most recent PypeIt sibling; `setup.py` + `requirements.txt` +
`pytest.ini`).  Created:

- `pypeit_chat/__init__.py` and `pypeit_chat/tests/__init__.py`
- `setup.py` in the house `setup_keywords` form, name `pypeit_chat`
- `requirements.txt`: numpy, pyyaml, tqdm, IPython, pytest, anthropic, with
  a note that the retrieval dependencies wait for the Context decision
- `pytest.ini`

I also rewrote `README.md` (goal, install, layout, authors) and appended a
`pypeit-chat` section to `.gitignore`:

- `.claude/settings.local.json`
- `.env.*`, with `!.env.example` so an example file can still be committed
- `*.key` and `secrets.toml`
- `index/` and `cache/`, for the search indices to come
- macOS and editor files

Checks run:

- `find_packages()` returns `['pypeit_chat', 'pypeit_chat.tests']`.
- `setup.py` reports its name and version.
- `pytest` collects 0 items cleanly.
- `git check-ignore` confirms that `.claude/settings.local.json`, `.env` and
  `.env.local` are ignored, while `.env.example` and `.claude/settings.json`
  are not.

What I learned: `anthropic` is **not installed in `pypeit14`**; run
`pip install anthropic` there before writing any chatbot code.  `.env` was
already in the stock template.  No git commands other than
`git check-ignore`/`git status` were run.

### 2026-10-28 (Context report on sources and design)

Executed task 1 under "Context" and wrote the Report above; no code was
edited apart from adding the measurement script.  Wrote
`pypeit_chat/survey_sources.py`, which measures the docs, the code (with
docstrings counted separately via `ast`) and the dev-suite PypeIt files, and
ran it in `pypeit14`.

What I learned:

- `doc/` is ~0.7M tokens, and 30% of it is the auto-generated
  `pypeit_par.rst`.
- `doc/api/` is only `automodule` stubs; the real API text is in the
  docstrings (~620k tokens).
- User Q&A happens on Slack, not GitHub.  Discussions is off, and only 5
  issues are labeled `question`.
- `snr_thresh` is defined in three ParSets with different defaults (1, 5,
  10), which makes it a good test of whether the bot grounds its answers.

I recommended a tool-using agent over a pinned checkout instead of an
embedding store, a CLI prototype and then a Slack bot, and an evaluation set
built from real Slack and issue questions.  Six questions for you, each with
a recommended answer, are at the end of the Report.  Only read-only `gh`
queries were run, to count issues; no git commands.

### 2026-10-28 (Applied answers to the Context questions)

Read your answers to the six questions in the Report and updated the repo to
match.  Added a "Decisions" block and a developer evaluation set after the
questions, and rewrote `## Goals` for a developer audience and a local model.

File changes:

- `CLAUDE.md`: Project now says the bot is for developers, records the
  open-weight local model, the release-tag rule (2.0.1 today) and the three
  open questions.  It also warns not to use the working PypeIt checkout as
  the corpus.
- `README.md`: developer audience, local model; removed the
  `ANTHROPIC_API_KEY` line.
- `requirements.txt`, `setup.py`: dropped `anthropic`; new description.
- `.claude/settings.json`: replaced the two Claude docs WebFetch domains with
  `huggingface.co` (model cards and weights).  The result has 43 allow
  entries and parses as JSON.
- `.gitignore`: added `models/`, `*.gguf` and `*.safetensors` for local
  weights.  `cache/` now also covers the release-tag checkout.

What I learned:

- The latest PypeIt tag is `2.0.1`.  The sibling checkout is on the feature
  branch `edge_2208`, 1374 commits past it, so the bot needs its own
  checkout at the tag.
- PypeIt has ~1420 PRs on GitHub.  For developer "why" questions they are
  probably a better source than the issues or Slack.
- `doc/dev/` has `new_spectrograph.rst`, `new_script.rst`,
  `development.rst` and per-instrument config/frames pages.  Ten spectrograph
  modules mention `snr_thresh`.
- Flagged in the Decisions block: pinning to a release tag is in tension
  with a developer audience, which works on `develop`.  Moving to an
  open-weight model makes tool-call reliability the main risk of the
  recommended design.  I suggested deciding retrieval with a head-to-head
  comparison on the evaluation set.

Only read-only `git` and `gh` queries were run; nothing was staged or
committed.
