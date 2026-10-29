# Getting started

## Goals

This repository will create and operate a chatbot that answers questions about
PypeIt, grounded in its documentation and source code.  The aim is a helper
that users (and developers) can ask "how do I reduce my X data?" or "what does
this parameter do?" and get an answer that cites the relevant docs or code,
rather than a guess.

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

## Logging

The "Logs" section will record Claude's work.  Please use the following format:

### <Date> (Short summary of the work)

<Detailed description of the work and what you learned>

### <Date> (Short summary of the work)

<Detailed description of the work and what you learned>

...

## Logs
