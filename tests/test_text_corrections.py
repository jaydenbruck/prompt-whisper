import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from text_corrections import build_initial_prompt, clean_transcript, collapse_repeats, strip_hallucinations
from whisper_worker import parse_args


def test_collapse_repeats_filler_to_one():
    assert collapse_repeats("the the the the cat") == "the cat"


def test_collapse_repeats_keeps_emphasis_pair():
    assert collapse_repeats("way way way way cleaner") == "way way cleaner"


def test_short_repeats_untouched():
    assert collapse_repeats("very very good") == "very very good"


def test_whole_utterance_hallucination_dropped():
    assert strip_hallucinations("Thank you.") == ""


def test_trailing_hallucination_dropped():
    assert strip_hallucinations("Ship it tomorrow. Thanks for watching!") == "Ship it tomorrow."


def test_mid_sentence_you_kept():
    assert clean_transcript("I will tell you later.") == "I will tell you later."


def test_initial_prompt_from_vocab():
    assert build_initial_prompt(["Kubernetes", "PostgreSQL"]) == "Kubernetes, PostgreSQL."
    assert build_initial_prompt([]) is None


def test_worker_args():
    assert parse_args(["small", "300", "--cpu"]) == ("small", 300.0, True)
    assert parse_args([]) == ("large-v3-turbo", 900.0, False)
