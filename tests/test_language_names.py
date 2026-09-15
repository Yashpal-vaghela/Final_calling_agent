import pytest
from agent.session.call_session import CallSession

def test_explicit_english():
    session = CallSession("test", preferred_language="hi")
    switched = session.update_language_if_requested("English")
    assert switched is True
    assert session.preferred_language == "en"

def test_explicit_hindi():
    session = CallSession("test", preferred_language="en")
    switched = session.update_language_if_requested("Hindi")
    assert switched is True
    assert session.preferred_language == "hi"

def test_explicit_gujarati():
    session = CallSession("test", preferred_language="en")
    switched = session.update_language_if_requested("Gujarati")
    assert switched is True
    assert session.preferred_language == "gu"

def test_mention_gujarati_no_switch():
    session = CallSession("test", preferred_language="en")
    switched = session.update_language_if_requested("Do you support Gujarati?")
    assert switched is False
    assert session.preferred_language == "en"

def test_difference_between_hindi_and_gujarati():
    session = CallSession("test", preferred_language="en")
    switched = session.update_language_if_requested("What is the difference between Hindi and Gujarati?")
    assert switched is False
    assert session.preferred_language == "en"

def test_english_aur_hindi_dono_support_karte_ho():
    session = CallSession("test", preferred_language="hi")
    switched = session.update_language_if_requested("English aur Hindi dono support karte ho?")
    # This should be classified as Hindi due to "aur", "dono", "karte", "ho"
    # Even though it contains "English", Hindi grammar is stronger.
    # It's already in hi, so switched will be False, but language will remain hi.
    assert switched is False
    assert session.preferred_language == "hi"

    # Let's test from en -> hi
    session2 = CallSession("test2", preferred_language="en")
    switched2 = session2.update_language_if_requested("English aur Hindi dono support karte ho?")
    assert switched2 is True
    assert session2.preferred_language == "hi"
