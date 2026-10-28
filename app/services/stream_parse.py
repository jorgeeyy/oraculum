"""Incremental extraction of the "answer" field from a streamed JSON object.

The generator's protocol is a JSON object ({"answer": ..., "status": ...,
"cited_aliases": [...]}) which is validated in full only when the stream
finishes. This module lets the API layer show answer text as it arrives:
it watches the growing buffer for the "answer" key and decodes the string
value incrementally, never emitting anything the final parse would reject.

Design rules:
- Deltas are best-effort UX; the final validated answer (from the complete
  parse) is always authoritative - callers must replace displayed text on
  completion if they diverge.
- An escape sequence split across feeds (trailing backslash, partial \\u
  escape) is never emitted until it can be decoded completely.
- If the key never appears (key order, unusual output) the extractor simply
  emits nothing; the caller still gets the full text at completion.
"""

import json
import re

# The key, optional whitespace, then the opening quote of the value.
_KEY_RE = re.compile(r'"answer"\s*:\s*"')


class AnswerStreamExtractor:
    """Feed raw stream chunks; receive decodable answer-text deltas."""

    def __init__(self) -> None:
        self._buf = ""
        self._value_start: int | None = None
        self._decoded_len = 0
        self.finished = False

    def feed(self, text: str) -> str:
        """Add a raw chunk; return newly decodable answer text ('' if none)."""
        if self.finished or not text:
            return ""
        self._buf += text
        if self._value_start is None:
            match = _KEY_RE.search(self._buf)
            if match is None:
                return ""
            self._value_start = match.end()

        value_start = self._value_start
        end, closed = self._scan(value_start)
        if end < 0:
            return ""  # incomplete escape at the tail; wait for more input
        raw = self._buf[value_start:end]
        try:
            decoded = json.loads(f'"{raw}"')
        except (json.JSONDecodeError, ValueError):
            # Invalid so far (e.g. raw control chars, truncated escape that
            # still parses as incomplete) - hold emission until it is valid.
            if closed:
                self.finished = True  # never decodable; give up silently
            return ""
        # json leaves surrogate escapes as two lone units; combine them so
        # the caller gets real characters (and SSE JSON stays encodable).
        # A lone half-pair means a split pair: wait for the rest of it.
        try:
            decoded = decoded.encode("utf-16", "surrogatepass").decode("utf-16")
        except UnicodeDecodeError:
            if closed:
                self.finished = True  # unpaired surrogate in a final value
            return ""
        delta = decoded[self._decoded_len :]
        self._decoded_len = len(decoded)
        if closed:
            self.finished = True
        return delta

    def _scan(self, value_start: int) -> tuple[int, bool]:
        """Find the value's end: (index of closing quote, True) or the
        longest decodable prefix end (never splitting an escape), (end, False).
        Returns (-1, False) when the tail holds an incomplete escape."""
        buf = self._buf
        pos = value_start
        while pos < len(buf):
            char = buf[pos]
            if char == "\\":
                if pos + 1 >= len(buf):
                    return -1, False  # lone trailing backslash: wait
                pos += 2
                continue
            if char == '"':
                return pos, True
            pos += 1
        return len(buf), False
