# Implementing the bot

## Goals

Implement the `pypeit-chat` Slack bot described in `DESIGN.md`: corpus
checkout, ingest and index, hybrid retrieval with docs and code pools,
answering through an OpenAI-compatible endpoint, the Slack front end with the
abuse protection of Section 11, logging, the evaluation harness, and the
deploy files.  The throw-away prototype in
`/Users/dpelliccia/PypeIt/PypeItChat/pypeit-chat` informs the work, but its
code is not assumed.

## Prompts

1. Following `DESIGN.md`, implement the pypeit-chat chatbot.  See also the
   prototype.  If you get stuck, ask questions in Q&A.  Log your work.

## Q&A

Questions for the maintainers, each with the answer I assumed so that work
could continue.  Answer after `>A.`; I will revise the code to match.

1. `CLAUDE.md` and the old `README.md` describe the audience as developers,
   the conda env as `pypeit14` and the related repositories under
   `/Users/xavier/...`.  `DESIGN.md` says the bot is for users.  On this
   machine the env is `pypeit-chat` and the repositories are under
   `/Users/dpelliccia/PypeIt/`.  I followed `DESIGN.md`, used the
   `pypeit-chat` env, and rewrote `README.md`.  I did not touch
   `CLAUDE.md`.  Should I update its Project, Environment and Related
   repositories sections?  [Assumed: you will.]
>A. You can update it, but the local paths don't need to be changed

2. `DESIGN.md` (2.1, 6.4, 12) says `snr_thresh` is in three ParSets with
   defaults 1.0, 5.0 and 10.0, and that 9 spectrograph modules set it.  At
   2.0.1 there are two (`AlignPar` 1.0, `FindObjPar` 10.0); the 5.0 one is
   `CubeExtractionPar`, which exists only on `develop`.  8 modules set it
   (the 9th, `gemini_gnirs.py`, is a commented-out line).  The unit tests
   keep a synthetic three-ParSet fixture, as Section 12 asks.  Should the
   design text be corrected?  [Assumed: yes, by you.]
>A. for now focus on what is available in release. When develop is merged into relese we can change that.

3. The Report's evaluation question 7, "Is the SOAR TripleSpec supported?",
   expects `tutorials/soar_triplespec_howto.rst`.  At 2.0.1 that page and
   `soar_tspec.py` do not exist; only `p200_tspec` does.  I kept the
   question as a test that the bot does not claim support, with
   `p200_tspec` as the expected source.  [Assumed: correct.]
>A. soar_tspec is on develop.

4. DESIGN M2 asks for >= 30 real user questions.  The set has 40: 10 from
   the Report, 15 rewritten from closed GitHub issues (the only real ones),
   10 hand-written parameter/script/consistency questions and 5 off-topic
   probes.  Can questions be taken from the PypeIt Users Slack (Open
   question 7)?  [Assumed: not until you say so.]
>A. Not for now.

5. The tool-using agent (DESIGN 7.2) is not implemented; v1 is the hybrid
   retrieval only.  The evaluation harness records everything needed to
   compare a second approach.  Build the agent arm now, or after you have
   reviewed the baseline?  [Assumed: after.]
>A. maybe later

6. Behaviour the design leaves open, and what I chose:
   - refusals (other team, allowlists, blocked, too long, rate limits) are
     ephemeral; "paused", "busy" and off-topic replies are posted in the
     thread;
   - admins are still answered while the bot is paused;
   - `help`, `pause`, `resume` and `status` do not count toward rate
     limits; off-topic questions do;
   - `ALLOWED_CHANNEL_IDS` does not apply to DMs (`ALLOWED_USER_IDS` and
     `BLOCKED_USER_IDS` do);
   - if `HOME_TEAM_ID` is empty, the bot's own workspace (from
     `auth.test`) is used, with a warning.

   [Assumed: all fine.]
>A.

7. The corpus is a blobless clone with a sparse checkout (`doc/` without
   images, `pypeit/` without `pypeit/data/`): 49 MB instead of ~640 MB at
   the tag.  Nothing indexed is lost.  [Assumed: fine.]
>A.

8. The off-topic filter (DESIGN 11.3.3) needs a "PypeIt term" test.  I
   used exact identifiers, spectrograph names, the parts of spectrograph
   family names (`keck`, `lris`, `soar`...) and a list of spectroscopy
   words (`DOMAIN_TERMS` in `index.py`), plus a best docs cosine below
   0.66.  The threshold separates the 5 off-topic and 35 on-topic
   questions, but the sample is small.  Real off-topic attempts from the
   beta should be added to the evaluation set.  [Assumed: fine for the
   private stage.]
>A. we'll do later

9. The consistency checks found real drift in the 2.0.1 docs, which could
   be fixed in PypeIt:
   - stale references: `doc/fluxing.rst` cites
     `pypeit.fluxcalibrate.apply_flux_calib` (it is in
     `specobj.py`/`specobjs.py`); `doc/calibrations/tilt.rst` cites
     `pypeit.images.buildimage.tiltimage.TiltImage` (it is in
     `buildimage.py`);
   - `.pypeit` settings that are not parameters at 2.0.1:
     `reduction_tips.rst` `[reduce][[findobj]] maxnumber` (now
     `maxnumber_sci`/`maxnumber_std`), `telluric.rst` `tellgridfile` (the
     code has `telgridfile`), `spectrographs/lbt_mods.rst`
     `min_slit_length` (the code has `minimum_slit_length`) and
     `[sensfunc] flatfile` (also in `tutorials/nirspec_high_howto.rst`),
     `spectrographs/keck_kcwi.rst` `flatfield_structure`,
     `calibrations/scattlight.rst` `scattlight_method`, `coadd1d.rst`
     `sensfuncfile`;
   - prose defaults the checks cannot parse: `reduction_tips.rst` says the
     `find_fwhm` default is 10 (the code has 5.0).

   The full list is rebuilt with every index, in `index/consistency.json`.
   [Assumed: you decide whether to fix them upstream.]
>A. We should fix them in the docs

10. N1 latency: the baseline median is above the design's targets (numbers
    in the log below).  Options: fewer or shorter sources (e.g.
    `DOC_SOURCES=5`, `CODE_SOURCES=3`, 1,500-character code excerpts),
    a smaller model, or prompt caching across a thread.  [Assumed: measure
    on the reference host before changing anything.]
>A.

## Logging

The "Logs" section records Claude's work.  Please use the following format:

### <Date> (Short summary of the work)

<Detailed description of the work and what you learned>

## Logs

### 2026-10-29 (Implemented the bot: ingest, retrieval, answering, Slack, evaluation)

Executed prompt 1.  Implemented `DESIGN.md` milestones M1–M3 and the deploy
files of M4, following the prototype in
`/Users/dpelliccia/PypeIt/PypeItChat/pypeit-chat` (its RST parser,
tokenizer/BM25 and reasoning filter were ported and restructured; the rest
is new).  No git commands other than read-only ones were run.

**Code** (`pypeit_chat/`, ~5,000 lines plus ~1,600 of tests):

- `config.py`: settings from the environment and `.env` (DESIGN 9), plus
  `THINK_SWITCH`, `LLM_TIMEOUT`, `CORPUS_URL`, `HASH_SALT`, `PAUSED_FILE`.
- `corpus.py`: blobless clone of `github.com/pypeit/PypeIt` in
  `cache/PypeIt` with a sparse checkout; latest tag from `git describe
  --tags --abbrev=0 origin/release`.
- `ingest/` (`rst`, `params`, `code`, `tags`, `xref`, `devsuite`,
  `build`): docs sections, one chunk per parameter (all `.pypeit` paths),
  one per spectrograph's documented defaults, `ast` code chunks, and the
  cross-references and consistency checks of DESIGN 6.4.  `xref` also
  resolves spectrograph classes through inheritance (`super()` calls) and
  maps spectrograph names to their `default_pypeit_par` and
  `config_specific_par`.  The index is written with `meta.json` last; it
  records the file sizes so a reader can detect a half-moved build.
- `index.py`: BM25 + cosine with RRF, run separately over the docs and
  code pools; identifier pinning (parameters, dotted/function names,
  scripts, spectrograph names, stale references); spectrograph boosts;
  diversity caps; cross-reference expansion that picks the linked chunk
  most relevant to the query; coverage and off-topic signals; reload on
  `meta.json` change.
- `llm.py`: OpenAI-compatible client (streaming chat, embeddings, models,
  health check).  `answer.py`: follow-up queries, prompt with DOCS/CODE
  labels, code facts, a list of known docs/code differences and the
  coverage note; reasoning filter; citation handling; version line.
- `store.py`: SQLite interactions, feedback, usage counters, event
  de-duplication, retention; salted user hashes; text only with
  `LOG_TEXT=1`.
- `slack/` (`app`, `guard`, `format`, `history`): Bolt + Socket Mode;
  every rule of DESIGN 11 (team boundary, bots/edits ignored, allowlists,
  blocklist, per-user/channel limits in SQLite, bounded queue with place
  in line and one active question per user, size limit, off-topic refusal
  without a model call, escaping, ping neutralizing, link allowlist,
  4,000-character cap, threads only, pause/resume, `cache/PAUSED`, admin
  alerts, weekly summary); `--check` health check.
- `ask.py`, `evaluate.py` and `bin/` (`pypeit_chat_ingest`, `_ask`,
  `_eval`, `_slack`); `deploy/` (Slack manifest, launchd service and
  weekly refresh, `refresh_index.sh`); `.env.example`; `README.md`
  rewritten; `requirements.txt` and `setup.py` gain `httpx` and
  `slack_bolt>=1.30`.
- Tests: 131 unit tests, 1.4 s, no network, model or Slack.  They use a
  fake PypeIt checkout (`tests/conftest.py`), a fake OpenAI-compatible
  server (`tests/mock_llm.py`: normal, slow, leaky, thinker and error
  models, usable over real HTTP too) and a fake Slack client.
- `tests/eval_questions.yaml`: 40 questions (see Q&A 4).

**Measured on this machine** (M4 Max, Ollama 0.40.13, `qwen3.8:27b-mlx`):

- Corpus: PypeIt 2.0.1 (`d5f8d74da`), 49 MB sparse checkout.
- Index: 1,613 docs sections, 426 parameters, 224 spectrograph-default
  chunks, 3,680 code objects; ~4,990 links; built in 100 s with
  embeddings (3.6 s without).  10 consistency findings (Q&A 9).
- Evaluation (`eval/2026-10-29_1617_qwen3.8-27b-mlx.json`): expected
  source in the top 10 for 94% of questions; both pools retrieved 100%;
  off-topic refused 5/5, no on-topic question refused; key facts 81%;
  expected source cited 91%; docs and code both cited 97% where expected;
  docs/code differences reported 2/2.  Latency: median 28.3 s for a
  complete answer and 17.8 s to first text, against targets of 30 s and
  15 s (Q&A 10).  Retrieval misses: `r09` (A-B differencing: the docs page
  is only reached via a docs-to-docs pointer) and `p05` ("which detectors
  are reduced": no stemming, so "reduced" does not match "reduction").

**What I learned about the corpus and the runtime**

- Ollama's OpenAI API turns reasoning off with `reasoning_effort: "none"`.
  It ignores `think: false` and streams reasoning in `delta.reasoning`.
- readthedocs serves `/en/2.0.1/`, and the anchors computed with docutils'
  `make_id` (e.g. `instr-par-keck-lris-red`) exist there.
- `pypeit_par.rst` is generated, so its defaults should match the code.
  The first comparison gave 77 "mismatches", all from my value
  normalization (strings containing commas, `(1, 2, 3),`, `(-5.0,5.0)`,
  `..` for empty values).  After the fix (`params.same_value`), none
  remain.  The real drift is in the hand-written pages (Q&A 9).
- The model faithfully repeats stale docs (it recommended `maxnumber`)
  unless the prompt carries the check.  Validating every `.pypeit` setting
  shown in the docs against the ParSets fixed that case: the answer now
  says "the docs show `maxnumber`, but the code in PypeIt 2.0.1 uses
  `maxnumber_sci`" and gives the code's `find_fwhm` default.  A flag on a
  source the model never sees is useless: pinning the stale docs chunk
  when a question names the symbol took the difference questions from 1/2
  (varying run to run) to 2/2.
- Off-topic questions reach a docs cosine of 0.64 with `nomic-embed-text`,
  as high as the lowest on-topic one ("Is the SOAR TripleSpec
  supported?").  The "no PypeIt term" test is what separates them.
- At 2.0.1 SOAR TripleSpec is not supported and `snr_thresh` has two
  ParSets, not three (Q&A 2, 3).
- The PypeIt docs links in the prototype went to `/en/release/`; the bot
  links to `/en/<tag>/` so answers and links match the stated version.

**Not done**: the tool-using agent arm (DESIGN 7.2, Q&A 5); anything that
needs Slack tokens (the bot has not yet connected to a real workspace:
M3's manual checklist in a private workspace is next); launchd installation;
more real evaluation questions (Q&A 4).

### 2026-10-30 (Applied the Q&A answers; fixed the stale PypeIt docs)

Read the answers to the Q&A.  Items 3–8 and 10 keep the assumed answers
(no agent arm or Slack-history questions for now, off-topic tuning later),
so the code is unchanged.  Changes:

- Item 1: rewrote the Project section of `CLAUDE.md` for the user audience
  and the decisions in `DESIGN.md`, and described the package layout.  The
  conda environment and the local paths are unchanged, as asked.  The
  Related repositories rule now allows writing to them when asked.
- Item 2 ("focus on what is available in release"): corrected `DESIGN.md`
  (2.1, 6.2, 6.4, 12): at 2.0.1, `snr_thresh` is in two ParSets and 8
  spectrograph modules set it.  The unit-test fixture keeps three ParSets
  to exercise the mechanism.  Also set the Status line, and added the new
  `.pypeit`-settings check to the list of consistency checks in 6.4.
- Item 9 ("fix them in the docs"): in the PypeIt repository
  (`/Users/dpelliccia/PypeIt/PypeIt`), created the local branch
  `docs_stale_params` from `origin/release` (PypeIt's convention for simple
  fixes) and edited 9 docs files, uncommitted:
  - `fluxing.rst`: `pypeit.fluxcalibrate.apply_flux_calib` ->
    `SpecObjs.apply_flux_calib` / `SpecObj.apply_flux_calib`;
  - `calibrations/tilt.rst`: `pypeit.images.buildimage.TiltImage`;
  - `reduction_tips.rst`: `maxnumber` -> `maxnumber_sci` (and the
    `maxnumber_std` default), `find_fwhm` default 10 -> 5;
  - `calibrations/scattlight.rst`: `scattlight_method` ->
    `[[[[scattlight]]]] method` (renamed in af78f3318, 2023);
  - `coadd1d.rst`: removed `sensfuncfile` (dropped in 0ee4b0bcb, 2023);
    echelle sensitivity files go in the data block's `sensfile` column;
  - `spectrographs/keck_kcwi.rst`: `flatfield_structure` ->
    `fit_2d_det_response` (renamed in 676ebd3d8, 2022);
  - `spectrographs/lbt_mods.rst`: `min_slit_length` ->
    `minimum_slit_length`; `[sensfunc] flatfile` -> `use_flat = True`
    (or `pypeit_sensfunc --use_flat`);
  - `tutorials/nirspec_high_howto.rst`: `flatfile` -> `use_flat`;
  - `telluric.rst`: `tellgridfile` -> `telgridfile`.

  All of them were also present on `origin/develop`.  Running the bot's
  consistency checks on the edited tree gives 0 findings (10 before).  The
  Sphinx docs were not rebuilt.  The bot's index is unchanged: it follows
  the 2.0.1 tag, so these findings disappear when a release with the fixes
  is tagged.

What I learned: the renames behind the stale settings date from 2022–2023,
so the hand-written pages had not been updated for several releases.
`git log -S"defaults['<key>']" -- pypeit/par/pypeitpar.py` finds the commit
that renamed or dropped a parameter quickly.

### 2026-10-30 (Moved the PypeIt docs fixes to a branch from develop)

You prefer PypeIt docs fixes on a branch from `develop`.  The 9 edited
files are identical on `release` and `develop`, so I stashed the edits,
recreated the local branch `docs_stale_params` from `origin/develop`
(`dc9f6cd2d`) and applied them; still uncommitted.  Also removed a trailing
space in `nirspec_high_howto.rst`.  `CLAUDE.md` now says docs fixes go on a
branch from `develop`.

Running the consistency checks on this develop-based tree confirms the 9
fixes and finds 12 newer problems that exist only on `develop`, not at
2.0.1, which I did not fix:

- 9 references to code that moved or was removed (e.g.
  `pypeit.bitmask.BitMask`, now in `pypeit/core/bitmask.py`;
  `pypeit.core.wavecal.templates`; `pypeit.core.flexure.MultiSlitFlexure`;
  `pypeit.qa.save_figure`/`flush_qa`; `pypeit.pypeitdata.PypeItDataPaths`;
  `pypeit.bspline.bspline.bspline`);
- `coadd3d.rst` shows `[reduce][[cube]] align`, which is not a parameter;
- `p200_ngps_g`/`_u`: the documented `spec_samp_coarse` (1.0, 2.0) differs
  from what the checker reads in `p200_ngps.py` L890 (5.0).  This one needs
  a closer look; it may be a checker limitation.

### 2026-10-30 (Fixed the develop-only stale docs; extended the checks)

Fixed, on the PypeIt branch `docs_stale_params` (from `origin/develop`,
uncommitted), the problems the checks found only on `develop`:

- `include/bitmask_usage.rst`, `include/bitmaskarray_usage.rst`:
  `pypeit.bitmask.BitMask` -> `pypeit.core.bitmask.BitMask`, including the
  `from ... import BitMask` lines in the examples;
- `calibrations/flexure.rst`: `pypeit.core.flexure.MultiSlitFlexure` ->
  `pypeit.multislit_flexure.MultiSlitFlexure`;
- `calibrations/construct_template.rst`, `calibrations/wave_calib.rst`:
  `pypeit/core/wavecal/templates.py` and `spectrographs/` moved to the
  Development Suite (`pypeitdev/wavecal/`, commit 996e57153).  Updated the
  paths and the import (`from pypeitdev.wavecal import templates`, tested in
  the `pypeit` env), and the run instruction (`python
  templ_TELESCOPE_INSTRUMENT.py`: each `templ_*` file has its own
  `__main__`);
- `coadd3d.rst`: `align = True` -> `alignment_method = phase` in the
  first example, and `alignment_method = user` in the one that gives offsets
  in the spec2d block (`align` was replaced in e03927e66);
- `releases/2.1.0dev.rst`: `build_template` (now in the dev suite),
  `QAWriter.save_figure`/`QAWriter.flush` (were `pypeit.qa` functions), and
  the legacy `bspline` class (now `pypeit.core.bspline.BSpline`).

Not changed: the 1.17.0 and 1.18.0 release notes, which correctly described
the code of their time.  The `p200_ngps` "mismatch" was not a docs bug: the
subclasses set `_flatfield_overrides`, which `default_pypeit_par` applies in a
loop (`par[...][k] = v`) after the literal value the checker had read.

Checker changes in `pypeit_chat` (3 new tests, 134 in total):

- assignments with a variable key mark their prefix as unreadable, so
  documented defaults under it are not compared (the NGPS case);
- references in past release notes are not flagged (in-development notes
  are);
- new `stale_import` check: `pypeit` imports in the docs' Python examples
  must resolve; names re-exported by a module (including inside
  `try`/`if`) count; `doc/dev/` is skipped.  I also tried every `from
  pypeit... import` line in the docs in the `pypeit` env: all import on
  `develop`;
- `rst.fenced_blocks` pairs code fences in order (a regex had paired a
  closing fence with the next opening one).

Results: the edited `develop` tree has 0 findings (12 before, 22 before any
fixes).  The 2.0.1 index still has its 10 findings, because the fixes are
not released yet.  `DESIGN.md` 6.4 lists the new checks and exclusions.

Found but not fixed, outside PypeIt: the Development Suite's 16
`pypeitdev/wavecal/spectrographs/templ_*.py` scripts still `from
pypeit.core.wavecal import templates`, which no longer exists on PypeIt
`develop`, so they fail to import (also on the dev suite's
`origin/develop`).  They need `from pypeitdev.wavecal import templates`.
