# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

This repository is used to create and operate a chatbot for **PypeIt
developers** (not users), grounded in PypeIt's source code and documentation.
Developers should be able to ask "where is X done?", "how do I add a new
spectrograph?" or "what does this parameter do?" and get an answer that cites
the relevant code or docs, rather than a guess.

Decisions so far (see the Report in `prompts/start_up.md`):

- The model is a public, open-weight model hosted locally on a Mac Studio,
  not the Claude API.  Code should talk to it through an OpenAI-compatible
  local endpoint, so that the model or runtime can be swapped.
- Answers are for the latest PypeIt release tag (2.0.1 as of 2026-10-28), and
  every answer says which version it is for.
- Still to be decided: where v1 runs, whether Users Slack history is used, and
  the retrieval approach.

The repository is new: at present it holds `README.md`, `LICENSE` (BSD
3-Clause), `.gitignore` (the stock GitHub Python template) and `prompts/`.
Structure will be added by the numbered tasks in `prompts/start_up.md`.

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
them.

- `/Users/xavier/Projects/PypeIt/PypeIt` — the PypeIt source. The Sphinx docs
  (`.rst`) are in `doc/` (developer docs in `doc/dev/`) and are published at
  https://pypeit.readthedocs.io; the code and its docstrings are in `pypeit/`.
  This is the user's working copy, usually on a feature branch.  Do not index
  it as the chatbot's corpus; the bot uses its own checkout at the release
  tag.
- `/Users/xavier/Projects/PypeIt/PypeIt-development-suite` — the development
  suite, for worked reduction examples (PypeIt files per instrument/setup).
