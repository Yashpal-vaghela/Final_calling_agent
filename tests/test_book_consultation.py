import pytest
from unittest.mock import patch, MagicMock
from agent.tools.book_consultation import book_consultation

def test_book_consultation_city_not_covered():
    # Attempt to book in an uncovered city
    result = book_consultation(
        first_name="John",
        last_name="Doe",
        phone="1234567890",
        email="john@example.com",
        city="UncoveredCity",
        doctor_name="",
        message="Test"
    )
    assert result["status"] == "city_not_covered"

def test_book_consultation_not_authorized():
    # City is covered, but doctor is not authorized
    result = book_consultation(
        first_name="John",
        last_name="Doe",
        phone="1234567890",
        email="john@example.com",
        city="Mumbai",
        doctor_name="Dr. Fake",
        message="Test"
    )
    assert result["status"] == "not_authorized"
    assert "The requested dentist is not an authorized smile designer" in result["message"]

@patch("agent.tools.book_consultation.httpx.post")
def test_book_consultation_success_with_doctor(mock_post):
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    # Mumbai has "Dr. Rohan Bandi" in our new data
    result = book_consultation(
        first_name="John",
        last_name="Doe",
        phone="1234567890",
        email="john@example.com",
        city="Mumbai",
        doctor_name="Dr. Rohan Bandi",
        message="Test"
    )
    assert result["status"] == "success"
    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs["json"]
    assert payload["doctor_name"] == "Dr. Rohan Bandi"
    assert payload["source"] == "calling_agent"
    assert payload["city"] == "Mumbai"

@patch("agent.tools.book_consultation.httpx.post")
def test_book_consultation_success_doctor_partial_name(mock_post):
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    # Surat has "Dr. Purvi Patel", user asks for "Purvi" or "Dr. Purvi"
    for doc_query in ["Purvi", "Dr. Purvi", "Doctor Purvi", "Dr Purvi Patel"]:
        mock_post.reset_mock()
        result = book_consultation(
            first_name="Keval",
            last_name="Patel",
            phone="9999999999",
            email="keval@example.com",
            city="Surat",
            doctor_name=doc_query,
            message="Test"
        )
        assert result["status"] == "success", f"Failed for {doc_query}"

    # Check that doctor_name was included in the payload
    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs["json"]
    assert payload["doctor_name"] == "Dr. Purvi Patel"
    assert payload["source"] == "calling_agent"
    assert payload["city"] == "Surat"
    assert set(payload.keys()) == {
        "lead_id",
        "first_name",
        "last_name",
        "phone",
        "email",
        "city",
        "doctor_name",
        "message",
        "source",
    }

@patch("agent.tools.book_consultation.httpx.post")
def test_book_consultation_success_no_doctor(mock_post):
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    result = book_consultation(
        first_name="John",
        last_name="Doe",
        phone="1234567890",
        email="john@example.com",
        city="Mumbai",
        doctor_name="",
        message="Test"
    )
    assert result["status"] == "success"
    
    # Check that doctor_name was omitted from the payload and source was added
    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs["json"]
    assert "doctor_name" not in payload
    assert payload["source"] == "calling_agent"
    assert set(payload.keys()) == {
        "lead_id",
        "first_name",
        "last_name",
        "phone",
        "email",
        "city",
        "message",
        "source",
    }

@patch("agent.tools.book_consultation.httpx.post")
def test_book_consultation_empty_last_name_defaults_safely(mock_post):
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    result = book_consultation(
        first_name="Keval",
        last_name="",  # Empty last name
        phone="1234567890",
        email="keval@example.com",
        city="Surat",
        message="Test single name"
    )
    assert result["status"] == "success"
    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs["json"]
    assert payload["last_name"] == "."
    assert payload["first_name"] == "Keval"

@patch("agent.tools.book_consultation.httpx.post")
def test_book_consultation_passes_lead_id_and_returns_it(mock_post):
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"data": {"id": 123}, "status": "success"}
    mock_post.return_value = mock_response

    result = book_consultation(
        first_name="Priya",
        last_name="Patel",
        phone="9876543210",
        email="priya@example.com",
        city="Surat",
        doctor_name="Dr. Jay Patel",
        message="Update test",
        lead_id="123"
    )
    assert result["status"] == "success"
    assert result["lead_id"] == "123"
    payload = mock_post.call_args.kwargs["json"]
    assert payload["lead_id"] == 123


