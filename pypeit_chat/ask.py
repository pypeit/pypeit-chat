"""
Ask from the terminal (DESIGN F11).  Run as ``pypeit_chat_ask``::

    pypeit_chat_ask "How do I make a PypeIt file for my Keck/LRIS red data?"
    pypeit_chat_ask --slack "..."      # print the exact Slack message (blocks JSON)
    pypeit_chat_ask --sources "..."    # retrieval only: the numbered sources
    pypeit_chat_ask                    # interactive; follow-ups keep the history
"""
import argparse
import json
import logging
import sys

from pypeit_chat.answer import Answerer, render_plain
from pypeit_chat.config import Settings
from pypeit_chat.index import IndexNotReady
from pypeit_chat.llm import LLMError
from pypeit_chat.slack import format as fmt


def show_sources(answerer, question, history):
    """Print the retrieval result for a question."""
    idx, query, r, warnings = answerer.retrieve(question, history)
    print(f'PypeIt {idx.tag} · {r.mode} search · spectrographs: {r.spectrographs or "-"} · '
          f'off-topic: {r.off_topic} · coverage: {r.coverage}')
    if query != question:
        print(f'query: {query!r}')
    for n, s in enumerate(r.sources, 1):
        c = s.chunk
        flags = ' ⚠' if c.get('flags') else ''
        print(f"[{n:2d}] {s.pool:4s} {s.reason:18s} {c['title'][:60]}{flags}\n"
              f"      {c['url']}")
    for w in warnings:
        print(f'(warning: {w})')


def ask_once(answerer, question, history, args):
    """Answer one question and print it; returns the Answer."""
    printed = ['']

    def on_text(text):
        if args.slack or args.quiet:
            return
        new = text[len(printed[0]):] if text.startswith(printed[0]) else '\n' + text
        sys.stdout.write(new)
        sys.stdout.flush()
        printed[0] = text

    ans = answerer.answer(question, history, on_text=on_text, think=args.think)
    if args.slack:
        msg = fmt.answer_message(ans) if ans.outcome == 'ok' else fmt.plain_message(ans.text)
        print(json.dumps(msg, indent=2, ensure_ascii=False))
        return ans
    out = render_plain(ans)
    if printed[0] and ans.outcome == 'ok' and out.startswith(ans.text):
        out = out[len(ans.text):]
        print()
    print(out)
    return ans


def parser():
    p = argparse.ArgumentParser(description='Ask pypeit-chat a question from the terminal.')
    p.add_argument('question', nargs='*', help='The question (interactive if omitted)')
    p.add_argument('--slack', action='store_true', help='Print the Slack message (blocks JSON)')
    p.add_argument('--sources', action='store_true', help='Show retrieval only (no model)')
    p.add_argument('--think', action='store_true', default=None, help='Reason before answering')
    p.add_argument('-q', '--quiet', action='store_true', help='Do not stream the answer')
    p.add_argument('-v', '--verbose', action='store_true', help='Debug logging')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    answerer = Answerer(Settings.load())
    history = []
    questions = [' '.join(args.question)] if args.question else None
    try:
        while True:
            if questions is not None:
                if not questions:
                    break
                q = questions.pop(0)
            else:
                try:
                    q = input('\nask> ').strip()
                except EOFError:
                    break
                if not q:
                    continue
            if args.sources:
                show_sources(answerer, q, history)
                continue
            ans = ask_once(answerer, q, history, args)
            if ans.outcome == 'ok':
                history += [{'role': 'user', 'content': q},
                            {'role': 'assistant', 'content': ans.text}]
    except (IndexNotReady, LLMError) as e:
        sys.exit(f'ERROR: {e}')
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
