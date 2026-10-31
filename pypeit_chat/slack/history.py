"""
Thread history for follow-up questions (DESIGN Section 8.3).

The bot's answers are tagged with Slack message metadata
(:data:`ANSWER_EVENT`), so only real answers (not progress or refusal
messages) become ``assistant`` turns.  Their notification text is the plain
answer, without sources or footer.
"""
import logging
import re

from pypeit_chat.answer import HISTORY_CHARS, HISTORY_TURNS, strip_citations, strip_mentions

log = logging.getLogger(__name__)

#: Metadata event type on the bot's answers.
ANSWER_EVENT = 'pypeit_chat_answer'


def unescape(text):
    """Undo Slack's escaping of ``&``, ``<`` and ``>``."""
    return text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')


def is_answer(msg, bot_user_id, bot_id):
    """True for one of the bot's answers."""
    ours = msg.get('user') == bot_user_id or (bot_id and msg.get('bot_id') == bot_id)
    meta = (msg.get('metadata') or {}).get('event_type')
    return bool(ours and meta == ANSWER_EVENT)


def messages_to_turns(messages, bot_user_id, bot_id=None, before_ts=None):
    """
    Convert thread messages to conversation turns.

    Parameters
    ----------
    messages : list of dict
        From ``conversations.replies``, oldest first.
    bot_user_id, bot_id : str
        Identify the bot's own messages.
    before_ts : str, optional
        Only messages older than this (the current question).

    Returns
    -------
    list of dict
        The last :data:`HISTORY_TURNS` ``{'role', 'content'}`` turns, each at
        most :data:`HISTORY_CHARS` characters.
    """
    turns = []
    for m in messages:
        if before_ts and float(m.get('ts', 0)) >= float(before_ts):
            continue
        if is_answer(m, bot_user_id, bot_id):
            text = strip_citations(unescape(m.get('text', '')))
            role = 'assistant'
        elif m.get('bot_id') or m.get('subtype') not in (None, 'thread_broadcast', 'file_share') \
                or m.get('user') == bot_user_id:
            continue
        else:
            text = strip_mentions(unescape(m.get('text', '')))
            role = 'user'
        text = re.sub(r'\s+\n', '\n', text).strip()
        if text:
            turns.append({'role': role, 'content': text[:HISTORY_CHARS]})
    return turns[-HISTORY_TURNS:]


def thread_history(client, channel, thread_ts, bot_user_id, bot_id=None, before_ts=None):
    """
    Fetch a thread and convert it to turns.  Errors give no history.

    Returns
    -------
    list of dict
    """
    try:
        resp = client.conversations_replies(channel=channel, ts=thread_ts, limit=50,
                                            include_all_metadata=True)
    except Exception as e:     # history is optional: answer without it
        log.warning('could not read thread %s/%s: %s', channel, thread_ts, e)
        return []
    return messages_to_turns(resp.get('messages', []), bot_user_id, bot_id, before_ts)
