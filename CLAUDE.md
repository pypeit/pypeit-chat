# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

This repository is used to create and operate **pypeit-chat**, a Slack bot
for **PypeIt users** (people reducing data with PypeIt), grounded in PypeIt's
documentation and source code.  Users ask "how do I make a PypeIt file for my
Keck/LRIS data?" or "what does this parameter do?" and get an answer that
cites the docs and code it is based on.  The repository itself is for the
bot's developers.  `DESIGN.md` is the design; follow it.

Decisions so far (see `DESIGN.md` Section 1 and the Q&A in
`prompts/start_up.md` and `prompts/implement.md`):

- The model is a public, open-weight model hosted locally, not the Claude
  API.  Code talks to it through an OpenAI-compatible endpoint, so the model
  or runtime can be swapped by configuration.
- Answers are for the latest PypeIt release tag (2.0.1 as of 2026-10-28),
  and every answer says which version it is for.  Work from what is on
  `release`; features only on `develop` (e.g. `soar_tspec`) are out of scope
  until they are released.
- v1 runs as a Slack bot with hybrid (BM25 + embedding) retrieval over a
  docs pool and a code pool.  A tool-using agent arm may come later.
- Slack history is not indexed and, for now, not used for evaluation
  questions.

The package is `pypeit_chat` (ingest, index, answering, Slack front end,
evaluation); the commands are in `bin/`, deploy files in `deploy/`, and the
tests in `pypeit_chat/tests` (run `pytest`; no network, model or Slack).
The bot's own PypeIt checkout and the index live in the git-ignored
`cache/` and `index/`.

## Workflow conventions

- **Git is handled by the user.** I (the user) will perform all git commands —
  staging, committing, branching, pushing, tagging, etc. Do not run `git add`,
  `git commit`, `git push`, `git reset`, `git rebase` or any other
  state-changing git command unless I explicitly ask. Read-only git inspection
  (`git status`, `git diff`, `git log`, `git show`, `git branch`) is fine.
- **Calculations become scripts on disk.** If you do any calculation, generate
  it as a Python script and write it to disk so that I can add it to the
  repository. Do not leave results as ephemeral inline snippets — they should be
  committable and re-runnable.
- **`prompts/` is the source of instruction.** Prompt docs live in `prompts/`
  (not `claude_prompts/`). Read the relevant one before acting, and do only the
  numbered task you were pointed at — not the whole file.
- **Log your work.** After finishing a task, append a dated entry under the
  `## Logs` section of the prompt doc you were working from, using the format
  given in that doc's `## Logging` section: `### YYYY-MM-DD (short summary)`
  followed by what was done *and what you learned about the repository*.

## Environment

- Python code runs in the `pypeit14` conda environment. Never the system Python.
  Invoke it as `conda run -n pypeit14 python ...`.

## Related repositories on disk

These are the chatbot's source material. Read from them freely; never write to
them unless asked (e.g. to fix stale docs found by the consistency checks,
on a branch from `develop` in the PypeIt repository).

- `/Users/xavier/Projects/PypeIt/PypeIt` — the PypeIt source. The Sphinx docs
  (`.rst`) are in `doc/` (developer docs in `doc/dev/`) and are published at
  https://pypeit.readthedocs.io; the code and its docstrings are in `pypeit/`.
  This is the user's working copy, usually on a feature branch.  Do not index
  it as the chatbot's corpus; the bot uses its own checkout at the release
  tag.
- `/Users/xavier/Projects/PypeIt/PypeIt-development-suite` — the development
  suite, for worked reduction examples (PypeIt files per instrument/setup).
