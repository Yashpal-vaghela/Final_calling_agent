import os
import urllib.request

MODEL_URL = "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"
TARGET_DIR = os.path.join(os.path.dirname(__file__), "..", "agent", "audio")
TARGET_PATH = os.path.join(TARGET_DIR, "silero_vad.onnx")

def download_model():
    os.makedirs(TARGET_DIR, exist_ok=True)
    if os.path.exists(TARGET_PATH):
        print(f"Model already exists at {TARGET_PATH}")
        return

    print(f"Downloading Silero VAD ONNX model from {MODEL_URL}...")
    try:
        urllib.request.urlretrieve(MODEL_URL, TARGET_PATH)
        print(f"Successfully downloaded to {TARGET_PATH}")
    except Exception as e:
        print(f"Failed to download model: {e}")
        if os.path.exists(TARGET_PATH):
            os.remove(TARGET_PATH)
        raise

if __name__ == "__main__":
    download_model()
