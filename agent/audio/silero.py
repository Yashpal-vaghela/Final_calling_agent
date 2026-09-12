import os
import numpy as np
import onnxruntime

class SileroVAD:
    """
    Lightweight ONNX inference wrapper for Silero VAD (v5+) without PyTorch.
    Manages the ONNX Runtime session globally and maintains per-call temporal state.
    """
    _global_session = None

    @classmethod
    def get_global_session(cls):
        if cls._global_session is None:
            model_path = os.path.join(os.path.dirname(__file__), "silero_vad.onnx")
            if not os.path.exists(model_path):
                # If model doesn't exist, we fallback or raise an error depending on integration
                raise FileNotFoundError(f"Silero VAD ONNX model not found at {model_path}. Run download script.")
            
            # Using ONNXRuntime InferenceSession
            cls._global_session = onnxruntime.InferenceSession(model_path)
        return cls._global_session

    def __init__(self, threshold: float = 0.5, min_silence_duration_ms: int = 500, sample_rate: int = 16000):
        self.session = self.get_global_session()
        self.threshold = threshold
        self.sample_rate = sample_rate
        
        # VAD Iterator sliding window state
        self.min_silence_samples = sample_rate * min_silence_duration_ms / 1000
        self.min_silence_duration_ms = min_silence_duration_ms
        self.triggered = False
        self.temp_end = 0
        self.current_sample = 0
        
        # Temporal state tensor for ONNX (shape [2, batch, 128] for v5+)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        # Sample rate tensor (int64)
        self._sr_tensor = np.array([self.sample_rate], dtype=np.int64)

    def reset_state(self):
        """Reset temporal state per call/session."""
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self.triggered = False
        self.temp_end = 0
        self.current_sample = 0

    def __call__(self, x: np.ndarray, return_seconds: bool = False):
        """
        Processes a single audio chunk and returns speech start/end events.
        x: float32 numpy array of shape [chunk_size].
           For 16kHz, chunk_size must be 512.
        """
        chunk_size = len(x)
        self.current_sample += chunk_size
        
        # Reshape to [batch, chunk_size] for ONNX
        x_batched = np.expand_dims(x, axis=0)

        # Run ONNX inference
        ort_inputs = {
            'input': x_batched,
            'sr': self._sr_tensor,
            'state': self._state
        }
        
        ort_outs = self.session.run(None, ort_inputs)
        # Output 0 is probability, Output 1 is the updated state
        speech_prob = ort_outs[0][0][0]  # shape was [batch, 1]
        self._state = ort_outs[1]

        # Implement VADIterator thresholding logic
        speech_dict = {}

        if speech_prob >= self.threshold and self.temp_end:
            self.temp_end = 0

        if speech_prob >= self.threshold and not self.triggered:
            self.triggered = True
            speech_dict['start'] = self.current_sample - chunk_size
        elif speech_prob < (self.threshold - 0.15) and self.triggered:
            if not self.temp_end:
                self.temp_end = self.current_sample
            if self.current_sample - self.temp_end < self.min_silence_samples:
                pass
            else:
                speech_dict['end'] = self.temp_end
                self.temp_end = 0
                self.triggered = False

        if return_seconds and speech_dict:
            return {k: round(v / self.sample_rate, 1) for k, v in speech_dict.items()}
        
        return speech_dict
