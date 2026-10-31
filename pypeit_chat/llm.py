"""
Client for an OpenAI-compatible endpoint: streaming ``/chat/completions``,
``/embeddings`` and ``/models``.

Only HTTP and the OpenAI wire format are assumed, so switching model or
runtime (Ollama, llama.cpp, mlx_lm.server, vLLM, LM Studio) is a
configuration change.  The one runtime-specific switch, reasoning on/off, is
chosen by ``THINK_SWITCH`` (:func:`think_options`).
"""
import json
import time

import httpx
import numpy as np

from pypeit_chat.config import embed_prefixes


class LLMError(Exception):
    """A model-server problem, with a message that is safe to show users."""


class LLMUnavailable(LLMError):
    """The model server cannot be reached."""


def think_options(switch, think):
    """
    Request fields that turn reasoning on or off for a runtime.

    Parameters
    ----------
    switch : str
        ``reasoning_effort`` (Ollama: ``"none"`` turns it off; ``think`` is
        ignored by its OpenAI API), ``chat_template_kwargs`` (vLLM,
        llama.cpp: ``enable_thinking``) or ``none`` (send nothing).
    think : bool
        Reason before answering.

    Returns
    -------
    dict
    """
    if switch == 'reasoning_effort':
        return {'reasoning_effort': 'medium' if think else 'none'}
    if switch == 'chat_template_kwargs':
        return {'chat_template_kwargs': {'enable_thinking': bool(think)}}
    return {}


def normalize(vecs):
    """L2-normalize the rows of a 2D array (zero rows stay zero)."""
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.where(norms == 0, 1, norms)


class LLMClient:
    """
    Talk to the model server.

    Parameters
    ----------
    settings : pypeit_chat.config.Settings
        Endpoint, models, temperature and thinking switch.
    transport : httpx.BaseTransport, optional
        For tests (e.g. ``httpx.MockTransport``).
    """

    def __init__(self, settings, transport=None):
        self.settings = settings
        self.base = settings.llm_base_url.rstrip('/')
        self.headers = {'Authorization': f'Bearer {settings.llm_api_key}'}
        self.transport = transport

    def _client(self, read):
        return httpx.Client(headers=self.headers, transport=self.transport,
                            timeout=httpx.Timeout(connect=5.0, read=read, write=30.0, pool=5.0))

    def _error(self, status, body, model):
        if status == 404 or 'not found' in body.lower():
            return LLMError(f"The model '{model}' is not available on the model server.")
        try:
            body = json.loads(body).get('error', body)
            if isinstance(body, dict):
                body = body.get('message', body)
        except (ValueError, AttributeError):
            pass
        return LLMError(f'The model server returned an error ({status}): {str(body)[:300]}')

    def models(self, timeout=5.0):
        """
        Model names served by the endpoint.

        Returns
        -------
        list of str

        Raises
        ------
        LLMUnavailable
        """
        try:
            with self._client(timeout) as c:
                r = c.get(f'{self.base}/models')
                r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMUnavailable(f'The model server at {self.base} is not responding.') from e
        return [m.get('id', '') for m in r.json().get('data', [])]

    def embed(self, texts, query=False, model=None, timeout=300.0):
        """
        Embed texts, adding the model's document or query prefix.

        Parameters
        ----------
        texts : list of str
        query : bool, optional
            Use the query prefix instead of the document prefix.
        model : str, optional
            Embedding model; default ``settings.embed_model``.
        timeout : float, optional

        Returns
        -------
        numpy.ndarray
            Float32, shape ``(len(texts), dim)``, rows L2-normalized.

        Raises
        ------
        LLMError
        """
        model = model or self.settings.embed_model
        prefix = embed_prefixes(model)[1 if query else 0]
        payload = {'model': model, 'input': [prefix + t for t in texts]}
        try:
            with self._client(timeout) as c:
                r = c.post(f'{self.base}/embeddings', json=payload)
        except httpx.ConnectError as e:
            raise LLMUnavailable(f'The model server at {self.base} is not responding.') from e
        except httpx.HTTPError as e:
            raise LLMError(f'Embedding request failed: {e}') from e
        if r.status_code >= 400:
            raise self._error(r.status_code, r.text, model)
        data = sorted(r.json()['data'], key=lambda d: d.get('index', 0))
        return normalize(np.asarray([d['embedding'] for d in data], dtype=np.float32))

    def chat_stream(self, messages, think=None, max_tokens=None):
        """
        Stream a chat completion.

        Parameters
        ----------
        messages : list of dict
            OpenAI-style messages.
        think : bool, optional
            Reasoning on/off; default ``settings.think``.
        max_tokens : int, optional

        Yields
        ------
        tuple
            ``('reasoning', text)``, ``('content', text)`` and finally
            ``('done', {'seconds', 'tokens', 'finish_reason'})``.

        Raises
        ------
        LLMUnavailable
            If the server cannot be reached.
        LLMError
            For any other failure.
        """
        s = self.settings
        payload = {'model': s.chat_model, 'messages': messages, 'stream': True,
                   'temperature': s.temperature,
                   'stream_options': {'include_usage': True},
                   **think_options(s.think_switch, s.think if think is None else think)}
        if max_tokens:
            payload['max_tokens'] = max_tokens
        t0 = time.monotonic()
        tokens, finish = None, None
        try:
            with self._client(s.llm_timeout) as c:
                with c.stream('POST', f'{self.base}/chat/completions', json=payload) as r:
                    if r.status_code >= 400:
                        raise self._error(r.status_code, r.read().decode(errors='replace'),
                                          s.chat_model)
                    for line in r.iter_lines():
                        if not line.startswith('data:'):
                            continue
                        data = line[5:].strip()
                        if data == '[DONE]':
                            break
                        d = json.loads(data)
                        if d.get('error'):
                            raise self._error(500, json.dumps(d['error']), s.chat_model)
                        if d.get('usage'):
                            tokens = d['usage'].get('completion_tokens', tokens)
                        for choice in d.get('choices') or []:
                            delta = choice.get('delta') or {}
                            reasoning = delta.get('reasoning') or delta.get('reasoning_content')
                            if reasoning:
                                yield 'reasoning', reasoning
                            if delta.get('content'):
                                yield 'content', delta['content']
                            finish = choice.get('finish_reason') or finish
        except httpx.ConnectError as e:
            raise LLMUnavailable(f'The model server at {self.base} is not responding.') from e
        except httpx.TimeoutException as e:
            raise LLMError('The model server stopped responding.') from e
        except httpx.HTTPError as e:
            raise LLMError(f'Lost the connection to the model server: {e}') from e
        yield 'done', {'seconds': round(time.monotonic() - t0, 1), 'tokens': tokens,
                       'finish_reason': finish}

    def check(self):
        """
        Health check: the endpoint answers and both models are served.

        Returns
        -------
        list of str
            Problems found (empty if healthy).
        """
        try:
            served = self.models()
        except LLMError as e:
            return [str(e)]

        def norm(m):
            return m if ':' in m else m + ':latest'
        served = {norm(m) for m in served}
        return [f"Model '{m}' is not served by {self.base}"
                for m in (self.settings.chat_model, self.settings.embed_model)
                if norm(m) not in served]
