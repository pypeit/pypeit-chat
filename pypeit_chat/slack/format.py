"""
Slack message formatting and output sanitizing (DESIGN Sections 8.2, 11.4).

Model text is untrusted.  It is escaped (``&``, ``<``, ``>``) before any
Slack markup is added, so the model cannot create mentions, channel links or
links of its own; ``@channel``-style words are neutralized; and only links
built by the bot or to allowed PypeIt sites are clickable.
"""
import re
from urllib.parse import urlparse

#: Slack's limit on a section block's text.
BLOCK_MAX = 3000
#: Answers are cut here, with "(truncated)".
ANSWER_MAX = 4000
#: Hosts (and path prefixes) whose URLs may be links.
ALLOWED_LINKS = (('pypeit.readthedocs.io', ''), ('github.com', '/pypeit/'),
                 ('pypeit.github.io', ''))
ZWSP = '​'
FOOTER_NOTE = 'AI answers can be wrong — check the sources'
DOC_KINDS = ('doc', 'param', 'override', 'example')

FENCE_RE = re.compile(r'^\s*```')
URL_RE = re.compile(r'\b(?:https?://|www\.)[^\s<>`)\]]+', re.I)
DOMAIN_RE = re.compile(r'(?<![\w@./-])(?:[a-z0-9-]+\.)+(?:com|org|net|io|edu|gov|co|ai|app|dev|'
                       r'info|biz|xyz|me|ly|gl|gg|ru|cn|uk|de|link|site|online|top|click)'
                       r'(?:/[^\s`]*)?(?![\w.-])', re.I)
PING_RE = re.compile(r'@(?=(?:channel|here|everyone)\b)', re.I)
MD_LINK_RE = re.compile(r'\[([^\]\n]+)\]\((\S+?)\)')


def escape(text):
    """Escape Slack's control characters."""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def link_allowed(url):
    """True if ``url`` may be a clickable link (DESIGN 11.4.3)."""
    try:
        u = urlparse(url if '://' in url else 'https://' + url)
    except ValueError:
        return False
    if u.scheme not in ('http', 'https') or u.username or u.password:
        return False
    host = (u.hostname or '').lower()
    return any(host == h and u.path.lower().startswith(p) for h, p in ALLOWED_LINKS)


def split_code(text):
    """
    Split Markdown into segments outside and inside code fences.

    Returns
    -------
    list of (bool, str)
        (is_code, text), with fences included in the code segments.
    """
    out, cur, code = [], [], False
    for line in text.split('\n'):
        if FENCE_RE.match(line):
            if not code:
                if cur:
                    out.append((False, '\n'.join(cur)))
                cur, code = [line], True
            else:
                cur.append(line)
                out.append((True, '\n'.join(cur)))
                cur, code = [], False
            continue
        cur.append(line)
    if cur:
        out.append((code, '\n'.join(cur + (['```'] if code else []))))
    return out


def _inline_code_split(line):
    """Split a line into (is_code, text) parts at inline backticks."""
    parts = re.split(r'(`[^`\n]+`)', line)
    return [(p.startswith('`') and p.endswith('`') and len(p) > 1, p) for p in parts if p]


def _prose(text, cite_urls):
    """Convert an escaped Markdown prose segment to sanitized mrkdwn."""
    out = []
    for line in text.split('\n'):
        m = re.match(r'^(\s*)#{1,6}\s+(.*)$', line)
        if m:
            line = f'{m.group(1)}**{m.group(2).strip()}**'
        m = re.match(r'^(\s*)[-*+]\s+(.*)$', line)
        if m:
            line = f"{m.group(1)}• {m.group(2)}"
        pieces = []
        for is_code, part in _inline_code_split(line):
            if is_code:
                pieces.append(part)
                continue
            part = PING_RE.sub('@' + ZWSP, part)
            part = MD_LINK_RE.sub(lambda mm: _md_link(mm.group(1), mm.group(2)), part)
            part = _outside_links(part, lambda p: URL_RE.sub(lambda mm: _bare_url(mm.group(0)),
                                                             p))
            part = _protect_domains(part)
            part = re.sub(r'\*\*(.+?)\*\*', lambda mm: f'\x01{mm.group(1)}\x01', part)
            part = re.sub(r'__(.+?)__', lambda mm: f'\x01{mm.group(1)}\x01', part)
            part = re.sub(r'(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])', r'_\1_', part)
            part = part.replace('\x01', '*')
            part = _cite_links(part, cite_urls)
            pieces.append(part)
        out.append(''.join(pieces))
    return '\n'.join(out)


def _outside_links(part, fn):
    """Apply ``fn`` to the text outside ``<…>`` links and inline code."""
    pieces = re.split(r'(<[^<>]*>|`[^`]*`)', part)
    return ''.join(p if p.startswith(('<', '`')) else fn(p) for p in pieces)


def _md_link(text, url):
    url = url.replace('&amp;', '&')
    if link_allowed(url):
        return f'<{escape(url)}|{text}>'
    return f'{text} (`{escape(url)}`)'


def _bare_url(url):
    raw = url.replace('&amp;', '&')
    if link_allowed(raw):
        return f'<{escape(raw)}>'
    return f'`{url}`'


def _protect_domains(part):
    """Wrap bare domains (which Slack would auto-link) in code, outside links."""
    return _outside_links(part, lambda p: DOMAIN_RE.sub(
        lambda m: m.group(0) if link_allowed(m.group(0)) else f'`{m.group(0)}`', p))


def _cite_links(part, cite_urls):
    if not cite_urls:
        return part

    def sub(m):
        n = int(m.group(1))
        url = cite_urls.get(n)
        return f'<{escape(url)}|[{n}]>' if url else m.group(0)
    return _outside_links(part, lambda p: re.sub(r'\[(\d+)\]', sub, p))


def to_mrkdwn(text, cite_urls=None):
    """
    Convert model Markdown to sanitized Slack mrkdwn.

    Parameters
    ----------
    text : str
        Model output (untrusted).
    cite_urls : dict, optional
        Source number -> URL; ``[n]`` becomes a link to it.

    Returns
    -------
    str
    """
    out = []
    for is_code, seg in split_code(escape(text)):
        if is_code:
            lines = seg.split('\n')
            # Slack ignores the language after ```; drop it.
            lines[0] = '```'
            out.append('\n'.join(lines))
        else:
            out.append(_prose(seg, cite_urls or {}))
    return '\n'.join(out)


def truncate(text, limit=ANSWER_MAX):
    """
    Cut Markdown at ``limit`` characters at a line break, closing any open
    code fence, and add "(truncated)".
    """
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit('\n', 1)[0] if '\n' in text[:limit] else text[:limit]
    if sum(1 for line in cut.split('\n') if FENCE_RE.match(line)) % 2:
        cut += '\n```'
    return cut + '\n(truncated)'


def split_blocks(mrkdwn, limit=BLOCK_MAX):
    """
    Split mrkdwn into pieces of at most ``limit`` characters at blank lines,
    or at line breaks inside long paragraphs and code blocks (closing and
    reopening fences).

    Returns
    -------
    list of str
    """
    if len(mrkdwn) <= limit:
        return [mrkdwn]
    pieces, cur, fence = [], [], False

    def size():
        return sum(len(l) + 1 for l in cur)

    for line in mrkdwn.split('\n'):
        if len(line) > limit - 10:
            line = line[:limit - 11] + '…'
        if cur and size() + len(line) + 5 > limit:
            pieces.append('\n'.join(cur + (['```'] if fence else [])))
            cur = ['```'] if fence else []
        if line.strip().startswith('```'):
            fence = not fence
        cur.append(line)
    if cur:
        pieces.append('\n'.join(cur))
    return [p for p in pieces if p.strip()]


def source_line(n, source):
    """``[6] <url|pypeit.core.skysub.global_skysub> · L504–629``."""
    c = source.chunk
    title = escape(c['title'])
    line = f"[{n}] <{escape(c['url'])}|{title}>"
    if c['kind'] == 'code' and c.get('codetype') != 'module':
        line += f" · L{c['start']}–{c['end']}"
    return line


def sources_blocks(items, heading):
    """Context blocks listing sources, grouped as Docs and Code."""
    blocks = []
    for group, kinds in (('Docs', DOC_KINDS), ('Code', ('code',))):
        rows = [source_line(k, s) for k, s in items if s.chunk['kind'] in kinds]
        if rows:
            text = f'*{heading} — {group}:* ' + '  ·  '.join(rows)
            blocks.append({'type': 'context',
                           'elements': [{'type': 'mrkdwn', 'text': text[:BLOCK_MAX]}]})
    return blocks


def footer_text(ans):
    """``PypeIt 2.0.1 · qwen3.8:27b-mlx · 9.8 s · AI answers can be wrong — check the sources``."""
    bits = [f'PypeIt {ans.tag}' if ans.tag else 'PypeIt', ans.model]
    if ans.seconds:
        bits.append(f'{ans.seconds:.1f} s')
    bits.append(FOOTER_NOTE)
    return ' · '.join(escape(b) for b in bits if b)


def answer_message(ans):
    """
    The Slack message for a finished answer.

    Parameters
    ----------
    ans : pypeit_chat.answer.Answer

    Returns
    -------
    dict
        ``text`` (notification fallback, plain answer) and ``blocks``.
    """
    numbered = ans.sources or ans.related
    cite_urls = {k: s.chunk['url'] for k, s in numbered}
    body = to_mrkdwn(truncate(ans.text), cite_urls)
    blocks = [{'type': 'section', 'text': {'type': 'mrkdwn', 'text': piece}}
              for piece in split_blocks(body)]
    if ans.flagged:
        blocks.append({'type': 'context', 'elements': [{
            'type': 'mrkdwn',
            'text': f'⚠ Docs and code differ here; the answer follows the code in '
                    f'PypeIt {escape(ans.tag)}.'}]})
    if ans.sources:
        blocks += sources_blocks(ans.sources, 'Sources')
    elif ans.related:
        blocks += sources_blocks(ans.related, 'Related')
    if ans.outcome == 'ok':
        blocks.append({'type': 'context',
                       'elements': [{'type': 'mrkdwn', 'text': footer_text(ans)}]})
    fallback = escape(truncate(ans.text, 2900))
    return {'text': fallback, 'blocks': blocks[:50]}


def progress_message(text):
    """A message being streamed: sanitized text in one section, no sources."""
    body = to_mrkdwn(truncate(text))
    return {'text': escape(truncate(text, 2900)),
            'blocks': [{'type': 'section', 'text': {'type': 'mrkdwn', 'text': p}}
                       for p in split_blocks(body)][:10]}


def plain_message(text):
    """A short bot message (status, refusal, help); ``text`` is trusted."""
    return {'text': text}
