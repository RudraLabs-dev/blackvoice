"""split_ready_sentences: the sentence chunker AISkill's streaming path uses
to speak a reply as it arrives, instead of waiting for all of it.
"""

from __future__ import annotations

from blackvoice.text import split_ready_sentences


def test_no_boundary_yet_returns_nothing_and_the_buffer_unchanged() -> None:
    sentences, remainder = split_ready_sentences("The capital of France is")
    assert sentences == []
    assert remainder == "The capital of France is"


def test_a_confirmed_sentence_is_split_off() -> None:
    sentences, remainder = split_ready_sentences("It is Paris. Anything else")
    assert sentences == ["It is Paris."]
    assert remainder == "Anything else"


def test_multiple_confirmed_sentences_in_one_call() -> None:
    sentences, remainder = split_ready_sentences(
        "It is Paris. France is in Europe. What else"
    )
    assert sentences == ["It is Paris.", "France is in Europe."]
    assert remainder == "What else"


def test_a_boundary_at_the_very_end_is_not_confirmed_yet() -> None:
    """The token after it has not streamed in yet - it might still turn out
    to be a decimal point once it does.
    """
    sentences, remainder = split_ready_sentences("The answer is 2.")
    assert sentences == []
    assert remainder == "The answer is 2."


def test_a_decimal_point_is_never_mistaken_for_a_sentence_end() -> None:
    sentences, remainder = split_ready_sentences("It costs 2.5 dollars and it")
    assert sentences == []
    assert remainder == "It costs 2.5 dollars and it"


def test_exclamation_and_question_marks_are_boundaries() -> None:
    sentences, remainder = split_ready_sentences("Watch out! Are you okay? I")
    assert sentences == ["Watch out!", "Are you okay?"]
    assert remainder == "I"


def test_feeding_the_remainder_back_in_eventually_confirms_it() -> None:
    """The realistic streaming loop: keep appending newly arrived text to
    whatever remainder came back last time.
    """
    buffer = "The answer is 2."
    sentences, buffer = split_ready_sentences(buffer)
    assert sentences == []

    buffer += "5 dollars. Anything else?"
    sentences, buffer = split_ready_sentences(buffer)
    assert sentences == ["The answer is 2.5 dollars."]
    assert buffer == "Anything else?"


def test_an_empty_buffer_is_fine() -> None:
    assert split_ready_sentences("") == ([], "")
