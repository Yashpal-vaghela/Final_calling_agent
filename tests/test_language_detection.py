import pytest
from agent.session.call_session import CallSession

def test_language_detection():
    # Initialize with default English
    session = CallSession(call_id="test1234")
    assert session.preferred_language == "en"

    # Test Native Gujarati
    switched = session.update_language_if_requested("હા, હું આવવા માંગુ છું")
    assert switched is True
    assert session.preferred_language == "gu"

    # Test Native Hindi
    switched = session.update_language_if_requested("हाँ, मुझे अपॉइंटमेंट चाहिए")
    assert switched is True
    assert session.preferred_language == "hi"

    # Test Romanized Hindi Fallback
    switched = session.update_language_if_requested("haan mujhe book karna hai")
    # should stay hi or switch to hi? It is already hi, so switched might be False
    assert session.preferred_language == "hi"
    assert switched is False # Didn't change from hi

    # Let's reset to en to test switch
    session.set_preferred_language("en")
    switched = session.update_language_if_requested("theek hai batao")
    assert switched is True
    assert session.preferred_language == "hi"

    # Test Romanized Gujarati Fallback
    session.set_preferred_language("en")
    switched = session.update_language_if_requested("kem cho tame")
    assert switched is True
    assert session.preferred_language == "gu"

    # Test Short Utterances
    session.set_preferred_language("en")
    switched = session.update_language_if_requested("haan")
    assert switched is True
    assert session.preferred_language == "hi"

    session.set_preferred_language("en")
    switched = session.update_language_if_requested("kem")
    assert switched is True
    assert session.preferred_language == "gu"

    # Test English (should not falsely trigger)
    session.set_preferred_language("hi")
    switched = session.update_language_if_requested("I want to book an appointment")
    assert switched is True
    assert session.preferred_language == "en"
    
    # Test single-word affirmations ("ha", "haa", "yes") retain active language in Gujarati
    session.set_preferred_language("gu")
    switched = session.update_language_if_requested("ha")
    assert switched is False
    assert session.preferred_language == "gu"

    switched = session.update_language_if_requested("haa")
    assert switched is False
    assert session.preferred_language == "gu"

    switched = session.update_language_if_requested("ha")
    assert switched is False
    assert session.preferred_language == "gu"

    # Test 'okay' switches to English from Gujarati
    switched = session.update_language_if_requested("okay")
    assert switched is True
    assert session.preferred_language == "en"

    # In Hindi: "ha", "haa" must stay in Hindi!
    session.set_preferred_language("hi")
    switched = session.update_language_if_requested("ha")
    assert switched is False
    assert session.preferred_language == "hi"

    switched = session.update_language_if_requested("haa")
    assert switched is False
    assert session.preferred_language == "hi"

    # Test 'okay' switches to English from Hindi
    switched = session.update_language_if_requested("okay")
    assert switched is True
    assert session.preferred_language == "en"

    # Hindi question with 'दांत' MUST stay in Hindi, NEVER flip to Gujarati!
    session.set_preferred_language("hi")
    switched = session.update_language_if_requested("दांत में दर्द है कितना खर्च होगा")
    assert switched is False
    assert session.preferred_language == "hi"

    # From English, Hindi question with 'दांत' must switch to Hindi
    session.set_preferred_language("en")
    switched = session.update_language_if_requested("दांत साफ कराने का कितना लगेगा")
    assert switched is True
    assert session.preferred_language == "hi"

    # From English initial state, opening with "ha" or "haa" switches to Gujarati
    session.set_preferred_language("en")
    switched = session.update_language_if_requested("haa")
    assert switched is True
    assert session.preferred_language == "gu"

    # Test Devanagari-transcribed Gujarati words detect as Gujarati
    session.set_preferred_language("hi")
    switched = session.update_language_if_requested("तमारे शु करवु छे")
    assert switched is True
    assert session.preferred_language == "gu"

    session.set_preferred_language("hi")
    switched = session.update_language_if_requested("केटला थशे")
    assert switched is True
    assert session.preferred_language == "gu"

    session.set_preferred_language("hi")
    switched = session.update_language_if_requested("હા બોલો")
    assert switched is True
    assert session.preferred_language == "gu"

    # Test Hindi distinguishes clearly from Gujarati
    session.set_preferred_language("gu")
    switched = session.update_language_if_requested("हाँ, मुझे जानकारी चाहिए")
    assert switched is True
    assert session.preferred_language == "hi"

    session.set_preferred_language("gu")
    switched = session.update_language_if_requested("haanji kitna lagega")
    assert switched is True
    assert session.preferred_language == "hi"

    # Test short English question after Hindi
    session.set_preferred_language("hi")
    switched = session.update_language_if_requested("What is the cost?")
    assert switched is True
    assert session.preferred_language == "en"

    # Test short Gujarati question after English
    switched = session.update_language_if_requested("ketlu thase?")
    assert switched is True
    assert session.preferred_language == "gu"

    # Test short English switch after Gujarati
    switched = session.update_language_if_requested("Okay, thank you")
    assert switched is True
    assert session.preferred_language == "en"

    # Test short Hindi switch after English
    switched = session.update_language_if_requested("kitna time lagega?")
    assert switched is True
    assert session.preferred_language == "hi"

    session.set_preferred_language("gu")
    switched = session.update_language_if_requested("random nonsense sound xyz")
    assert switched is False
    assert session.preferred_language == "gu"

    # Test 'okay then' switches from Hindi to English
    session.set_preferred_language("hi")
    switched = session.update_language_if_requested("okay then")
    assert switched is True
    assert session.preferred_language == "en"

    # Test 'ok then' switches from Gujarati to English
    session.set_preferred_language("gu")
    switched = session.update_language_if_requested("ok then")
    assert switched is True
    assert session.preferred_language == "en"

    # Test 'alright' switches to English
    session.set_preferred_language("hi")
    switched = session.update_language_if_requested("alright")
    assert switched is True
    assert session.preferred_language == "en"

    # Test Indic with okay retains Hindi if Hindi triggers present
    session.set_preferred_language("hi")
    switched = session.update_language_if_requested("okay bataiye")
    assert switched is False
    assert session.preferred_language == "hi"

    # Test 'ओके, समझी गयो।' switches from Hindi to Gujarati (not English!)
    session.set_preferred_language("hi")
    switched = session.update_language_if_requested("ओके, समझी गयो।")
    assert switched is True
    assert session.preferred_language == "gu"

    # Test 'Haan. Nahin hai.' switches from Gujarati to Hindi
    session.set_preferred_language("gu")
    switched = session.update_language_if_requested("Haan. Nahin hai.")
    assert switched is True
    assert session.preferred_language == "hi"

if __name__ == "__main__":
    pytest.main(["-v", __file__])
