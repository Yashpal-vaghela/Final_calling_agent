import onnxruntime
import sys

try:
    session = onnxruntime.InferenceSession("agent/audio/silero_vad.onnx")
    print("Inputs:")
    for i in session.get_inputs():
        print(f"  Name: {i.name}, Shape: {i.shape}, Type: {i.type}")
    print("Outputs:")
    for o in session.get_outputs():
        print(f"  Name: {o.name}, Shape: {o.shape}, Type: {o.type}")
except Exception as e:
    print(f"Error: {e}")
