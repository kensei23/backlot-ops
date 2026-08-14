"""
Converts text (the Line Producer's stakeholder message) into a spoken
audio alert using Gemini TTS, saved as a WAV file.
"""

import os
import wave

from google import genai
from google.genai import types


def _save_wave(filename: str, pcm_data: bytes, channels=1, rate=24000, sample_width=2):
    """Vertex AI's TTS output is raw PCM audio with no WAV header, so we
    wrap it into a proper .wav file ourselves before it can be played.
    """
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(rate)
        wf.writeframes(pcm_data)


def text_to_speech(text: str, output_path: str = "alert.wav", voice: str = "Kore") -> str:
    """Generate a spoken audio alert from text, saved to output_path.
    Returns the path on success.
    """
    client = genai.Client(
        vertexai=True,
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
    )

    response = client.models.generate_content(
        model="gemini-3.1-flash-tts-preview",
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                )
            ),
        ),
    )

    pcm_data = response.candidates[0].content.parts[0].inline_data.data
    _save_wave(output_path, pcm_data)
    return output_path


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    test_text = (
        "We're experiencing some render farm instability. This may cause "
        "minor delays. We are monitoring the situation closely."
    )
    path = text_to_speech(test_text)
    print(f"Saved audio to: {path}")
