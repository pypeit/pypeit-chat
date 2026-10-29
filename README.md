# pypeit-chat

Create and operate a chatbot for the [PypeIt](https://github.com/pypeit/PypeIt)
docs and code.

The goal is a helper for PypeIt developers: ask "where is X done?", "how do I
add a new spectrograph?" or "what does this parameter do?" and get an answer
that cites the relevant source code or
[documentation](https://pypeit.readthedocs.io), rather than a guess.  Answers
are for the latest PypeIt release.

## Installation

```
conda activate pypeit14
pip install -r requirements.txt
pip install -e .
```

The chatbot runs on a public, open-weight model served locally (on a Mac
Studio) through an OpenAI-compatible endpoint.

## Layout

- `pypeit_chat/` — the Python package (`tests/` beneath it)
- `prompts/` — task prompts and a dated log of the work
- `CLAUDE.md` — conventions for working in this repository

## Authors

- J. Xavier Prochaska
- Claude
