"""
A fake OpenAI-compatible model server, for tests without a real model.

Models (the ``model`` field of a request):

- ``mock``: streams :data:`ANSWER`, citing sources [1] and [2];
- ``slow``: the same, slowly;
- ``leaky``: writes reasoning into the answer, followed by a stray
  ``</think>`` (as some Qwen builds do with thinking off);
- ``thinker``: sends reasoning in ``delta.reasoning``, then the answer;
- ``error``: returns HTTP 500.

Embeddings are hashed bags of words, so similar texts get similar vectors.

In tests, pass :func:`transport` to :class:`pypeit_chat.llm.LLMClient`.  To
run a real server for ``pypeit_chat_ask`` or the Slack bot::

    python -m pypeit_chat.tests.mock_llm 11435
    LLM_BASE_URL=http://127.0.0.1:11435/v1 CHAT_MODEL=mock pypeit_chat_ask "..."
"""
import hashlib
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import numpy as np

from pypeit_chat.index import tokenize

DIM = 64
MODELS = ['mock', 'slow', 'leaky', 'thinker', 'error', 'nomic-embed-text']
ANSWER = """\
Set `bspline_spacing` under `[reduce][skysub]` in your PypeIt file [1]; the code \
default in SkySubPar is 0.6 [2].

```ini
[reduce]
    [[skysub]]
        bspline_spacing = 0.8
```
"""
REASONING = 'The user asks about sky subtraction. Source [1] is the parameter.'


def embed_one(text):
    """Deterministic hashed bag-of-words vector."""
    v = np.zeros(DIM, dtype=np.float32)
    for tok in tokenize(text):
        h = int.from_bytes(hashlib.md5(tok.encode()).digest()[:4], 'little')
        v[h % DIM] += 1.0
    n = np.linalg.norm(v)
    return (v / n if n else v).tolist()


def sse(obj):
    """One server-sent event."""
    return f'data: {json.dumps(obj)}\n\n'.encode()


def chunk(content=None, reasoning=None, finish=None):
    delta = {}
    if content is not None:
        delta['content'] = content
    if reasoning is not None:
        delta['reasoning'] = reasoning
    return {'object': 'chat.completion.chunk',
            'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}


def chat_events(body):
    """
    The streamed events for a chat request.

    Returns
    -------
    tuple
        (status, list of bytes, delay between pieces in seconds)
    """
    model = body.get('model', '').split(':')[0]
    if model not in MODELS:
        return 404, [json.dumps({'error': {'message': f"model '{model}' not found"}}).encode()], 0
    if model == 'error':
        return 500, [json.dumps({'error': {'message': 'boom'}}).encode()], 0
    events = []
    if model == 'thinker':
        events += [sse(chunk(reasoning=w + ' ')) for w in REASONING.split()]
    text = ANSWER
    if model == 'leaky':
        text = 'Let me look at source [1] first. </think>\n\n' + ANSWER
    events += [sse(chunk(content=text[i:i + 7])) for i in range(0, len(text), 7)]
    events.append(sse(chunk(finish='stop')))
    events.append(sse({'choices': [], 'usage': {'completion_tokens': len(text) // 4}}))
    events.append(b'data: [DONE]\n\n')
    return 200, events, 0.05 if model == 'slow' else 0.0


def embeddings(body):
    """Response body for an embeddings request."""
    inputs = body['input'] if isinstance(body['input'], list) else [body['input']]
    return {'object': 'list', 'model': body.get('model'),
            'data': [{'object': 'embedding', 'index': i, 'embedding': embed_one(t)}
                     for i, t in enumerate(inputs)]}


class Recorder:
    """Keeps the requests a mock transport received (for assertions)."""

    def __init__(self):
        self.requests = []


def transport(recorder=None, down=False):
    """
    An ``httpx.MockTransport`` serving the fake endpoint.

    Parameters
    ----------
    recorder : Recorder, optional
        Collects ``(path, json body)`` of every request.
    down : bool, optional
        Simulate an unreachable server (every request raises ConnectError).
    """
    def handler(request):
        if down:
            raise httpx.ConnectError('connection refused', request=request)
        body = json.loads(request.content or b'{}')
        path = request.url.path
        if recorder is not None:
            recorder.requests.append((path, body))
        if path.endswith('/models'):
            return httpx.Response(200, json={'data': [{'id': m} for m in MODELS]})
        if path.endswith('/embeddings'):
            return httpx.Response(200, json=embeddings(body))
        if path.endswith('/chat/completions'):
            status, events, delay = chat_events(body)
            if status != 200:
                return httpx.Response(status, content=events[0])

            def stream():
                for e in events:
                    if delay:
                        time.sleep(delay)
                    yield e
            return httpx.Response(200, content=stream(),
                                  headers={'content-type': 'text/event-stream'})
        return httpx.Response(404, json={'error': 'not found'})
    return httpx.MockTransport(handler)


class Handler(BaseHTTPRequestHandler):
    """The same endpoint over real HTTP."""

    def _json(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.endswith('/models'):
            self._json(200, {'data': [{'id': m} for m in MODELS]})
        else:
            self._json(404, {'error': 'not found'})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
        if self.path.endswith('/embeddings'):
            self._json(200, embeddings(body))
            return
        if not self.path.endswith('/chat/completions'):
            self._json(404, {'error': 'not found'})
            return
        status, events, delay = chat_events(body)
        if status != 200:
            self._json(status, json.loads(events[0]))
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for e in events:
            time.sleep(delay)
            self.wfile.write(e)
            self.wfile.flush()

    def log_message(self, *args):
        pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    port = int(argv[0]) if argv else 11435
    print(f'Mock OpenAI-compatible server on http://127.0.0.1:{port}/v1')
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()


if __name__ == '__main__':
    main()
