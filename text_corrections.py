"""
Transcription cleanup for Prompt Whisper.

Whisper (and faster-whisper) reliably produces two classes of error on
real-world dictation:

  1. Silence hallucinations - trailing junk like "you", "Thank you.",
     "Thanks for watching", "Please subscribe" invented from near-silence.
  2. Repetition spam        - a word repeated many times in a row
     ("the the the the").

`clean_transcript()` runs a conservative post-processing pass for both:
collapse repetition, then strip standalone/trailing hallucination fragments.

Misspelled names are handled at transcription time instead: list them in
WHISPER_VOCAB (see config.py) and `build_initial_prompt()` feeds them to
Whisper as a spelling hint.
"""
import re

from config import WHISPER_VOCAB


def build_initial_prompt(vocab=None):
    """Return the initial_prompt string for Whisper, or None if there is no vocab."""
    vocab = WHISPER_VOCAB if vocab is None else vocab
    if not vocab:
        return None
    return ", ".join(vocab) + "."


# Stripped ONLY when they are the whole utterance or a clean trailing fragment
# after a sentence boundary - never mid-sentence (that would eat legitimate
# "tell you", "for you", ...).
_HALLUCINATION_PHRASES = {
    "you",
    "thank you",
    "thanks for watching",
    "please subscribe",
    "subscribe",
    "bye",
    "bye bye",
    "deutsche bibelgesellschaft",   # well-known German Whisper silence artifact
    "untertitel im auftrag des zdf",
}

# Pure-filler words that are collapsed all the way to a single occurrence when
# spammed. Everything else collapses to at most two (preserves real emphasis
# like "way way cleaner").
_COLLAPSE_TO_ONE = {"you", "the", "uh", "um", "a"}

_REPEAT_MIN = 3  # a run of this many identical words (or more) is spam


def _norm_word(w: str) -> str:
    """Lowercase, strip surrounding punctuation. Keeps unicode letters."""
    return re.sub(r"[^\w]", "", w.lower(), flags=re.UNICODE)


def _norm_phrase(s: str) -> str:
    """Lowercase, drop punctuation, squeeze whitespace - for phrase matching."""
    s = re.sub(r"[^\w\s]", "", s.lower(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def collapse_repeats(text: str) -> str:
    """Collapse runs of the same word repeated 3+ times in a row."""
    tokens = text.split()
    if len(tokens) < _REPEAT_MIN:
        return text

    out = []
    i, n = 0, len(tokens)
    while i < n:
        base = _norm_word(tokens[i])
        j = i + 1
        while base and j < n and _norm_word(tokens[j]) == base:
            j += 1
        run = tokens[i:j]
        if len(run) >= _REPEAT_MIN:
            keep = 1 if base in _COLLAPSE_TO_ONE else 2
            out.extend(run[:keep])
        else:
            out.extend(run)
        i = j
    return " ".join(out)


def strip_hallucinations(text: str) -> str:
    """Remove whole-utterance and trailing-fragment hallucination phrases."""
    text = text.strip()
    if not text:
        return text

    if _norm_phrase(text) in _HALLUCINATION_PHRASES:
        return ""

    # Split into sentences (keeping their delimiters) and peel hallucination
    # fragments off the end.
    parts = re.split(r"([.!?]+)", text)
    sentences = []  # list of (segment, delimiter)
    k = 0
    while k < len(parts):
        seg = parts[k]
        delim = parts[k + 1] if k + 1 < len(parts) else ""
        if seg.strip() or delim:
            sentences.append((seg, delim))
        k += 2

    while sentences:
        seg, _ = sentences[-1]
        if _norm_phrase(seg) in _HALLUCINATION_PHRASES:
            sentences.pop()
        else:
            break

    return "".join(seg + delim for seg, delim in sentences).strip()


def clean_transcript(text: str) -> str:
    """Collapse repetition spam, then strip standalone/trailing hallucinations."""
    if not text:
        return text
    text = collapse_repeats(text.strip())
    text = strip_hallucinations(text)
    return re.sub(r"\s+", " ", text).strip()
