import pytest
from agent.session.call_session import CallSession

def test_mixed_appointment_book_kardo():
    session = CallSession("test_id", preferred_language="en")
    switched = session.update_language_if_requested("haan appointment book kardo")
    assert switched is True
    assert session.preferred_language == "hi"

def test_mixed_please_mujhe_batao():
    session = CallSession("test_id", preferred_language="en")
    switched = session.update_language_if_requested("please mujhe batao")
    assert switched is True
    assert session.preferred_language == "hi"

def test_mixed_veneers_samjavo():
    session = CallSession("test_id", preferred_language="en")
    switched = session.update_language_if_requested("veneers samjavo")
    assert switched is True
    assert session.preferred_language == "gu"

def test_mixed_appointment_karvu_che():
    session = CallSession("test_id", preferred_language="en")
    switched = session.update_language_if_requested("appointment karvu che")
    assert switched is True
    assert session.preferred_language == "gu"

def test_pure_english_want_to_book():
    session = CallSession("test_id", preferred_language="hi")
    switched = session.update_language_if_requested("I want to book an appointment")
    assert switched is True
    assert session.preferred_language == "en"

def test_pure_english_need_veneers():
    session = CallSession("test_id", preferred_language="hi")
    switched = session.update_language_if_requested("I need veneers")
    assert switched is True
    assert session.preferred_language == "en"

def test_dental_loanwords_are_neutral():
    session = CallSession("test_id", preferred_language="hi")
    # Should ignore 'appointment', leaving nothing -> fallback to current lang
    switched = session.update_language_if_requested("appointment")
    assert switched is False
    assert session.preferred_language == "hi"

def test_gu_to_hi():
    session = CallSession("test_id", preferred_language="gu")
    switched = session.update_language_if_requested("haan batao")
    assert switched is True
    assert session.preferred_language == "hi"

def test_gu_to_en():
    session = CallSession("test_id", preferred_language="gu")
    switched = session.update_language_if_requested("what is the cost")
    assert switched is True
    assert session.preferred_language == "en"

def test_hi_to_gu():
    session = CallSession("test_id", preferred_language="hi")
    switched = session.update_language_if_requested("shu che")
    assert switched is True
    assert session.preferred_language == "gu"

def test_hi_to_en():
    session = CallSession("test_id", preferred_language="hi")
    switched = session.update_language_if_requested("can you tell me")
    assert switched is True
    assert session.preferred_language == "en"

def test_en_to_gu():
    session = CallSession("test_id", preferred_language="en")
    switched = session.update_language_if_requested("kem cho")
    assert switched is True
    assert session.preferred_language == "gu"

def test_en_to_hi():
    session = CallSession("test_id", preferred_language="en")
    switched = session.update_language_if_requested("kya haal hai")
    assert switched is True
    assert session.preferred_language == "hi"

def test_single_word_haan():
    session = CallSession("test_id", preferred_language="en")
    switched = session.update_language_if_requested("haan")
    assert switched is False
    assert session.preferred_language == "en"

def test_single_word_haa():
    session = CallSession("test_id", preferred_language="en")
    switched = session.update_language_if_requested("haa")
    assert switched is False
    assert session.preferred_language == "en"

def test_gujarati_with_shared_dant_and_kharch():
    session = CallSession("test_id", preferred_language="en")
    switched = session.update_language_if_requested("dant no kharcha ketlo thase?")
    assert switched is True
    assert session.preferred_language == "gu"

def test_gujarati_with_namaste_and_vat():
    session = CallSession("test_id", preferred_language="en")
    switched = session.update_language_if_requested("namaste mane doctor sathe vat karvi che")
    assert switched is True
    assert session.preferred_language == "gu"

def test_gujarati_saru_ho_does_not_flip_to_hindi():
    session = CallSession("test_id", preferred_language="gu")
    switched = session.update_language_if_requested("saru ho")
    assert switched is False
    assert session.preferred_language == "gu"

def test_gujarati_ha_and_booking():
    session = CallSession("test_id", preferred_language="en")
    switched = session.update_language_if_requested("ha mara mate appointment book karo")
    assert switched is True
    assert session.preferred_language == "gu"

def test_gujarati_theek_che_samjavo():
    session = CallSession("test_id", preferred_language="en")
    switched = session.update_language_if_requested("theek che mane samjavo")
    assert switched is True
    assert session.preferred_language == "gu"

