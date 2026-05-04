"""
Interactive test for the audio capture + transcription pipeline.

Usage:
    .venv/bin/python3 pipeline.py
    .venv/bin/python3 pipeline.py --model small --device 3

Press Enter to start recording, Enter again to stop and transcribe.
Press Ctrl+C to quit.
"""

import argparse
import time
import sys

from recorder import AudioRecorder
from transcriber import Transcriber


def parse_args():
    p = argparse.ArgumentParser(description="TalkTalk pipeline test")
    p.add_argument("--model", default="base", help="Whisper model size (tiny/base/small/medium/large)")
    p.add_argument("--device", default=None, type=int, help="Microphone device index (default: system default)")
    p.add_argument("--language", default="en", help="Language code (default: en)")
    return p.parse_args()


def main():
    args = parse_args()

    recorder = AudioRecorder(device=args.device)
    transcriber = Transcriber(model_size=args.model, language=args.language)

    print("\nReady. Press Enter to start recording, Enter again to stop.\n")

    try:
        while True:
            input("  [Enter] to record...")
            recorder.start()
            print("  Recording... [Enter] to stop")
            input()

            print("  Processing...")
            t0 = time.perf_counter()
            audio = recorder.stop()
            duration = len(audio) / recorder.sample_rate

            if duration < 0.5:
                print("  (too short, skipping)\n")
                continue

            print(f"  Captured {duration:.1f}s of audio — transcribing...")
            transcript, lang = transcriber.transcribe(audio, sample_rate=recorder.sample_rate)
            elapsed = time.perf_counter() - t0

            print(f"\n  Transcript ({elapsed:.1f}s) [{lang}]:\n  {transcript!r}\n")

    except KeyboardInterrupt:
        print("\nBye.")
        sys.exit(0)


if __name__ == "__main__":
    main()
