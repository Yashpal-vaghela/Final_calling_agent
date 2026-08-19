import os
import sys
import urllib.parse
import webbrowser

# Ensure project root directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_smartflo_outbound.py <customer_number>")
        print("Example: python scripts/test_smartflo_outbound.py +918758657212")
        sys.exit(1)

    customer_number = sys.argv[1].strip()
    encoded_phone = urllib.parse.quote(customer_number)
    form_url = f"http://localhost:8050/contact-form?phone={encoded_phone}"

    print("\n" + "=" * 65)
    print("    ULTIMATE SMILE DESIGN — OUTBOUND CALL CONSULTATION FORM")
    print("=" * 65)
    print(f"Target Destination Phone : {customer_number}")
    print(f"Opening Contact Form URL : {form_url}")
    print("=" * 65)
    print("Please enter the caller's Name, Email, City, Subject, and Message")
    print("in the browser form and click 'Submit Details & Start Call'.")
    print("=" * 65 + "\n")

    try:
        webbrowser.open(form_url)
    except Exception as e:
        print(f"[Notice] Could not automatically open default browser: {e}")
        print(f"Please copy and open this URL manually in your browser:\n  {form_url}\n")


if __name__ == "__main__":
    main()
