import type { Page } from "@playwright/test";

// 20 ms of silence encoded with PyAV/libopus, matching the API test fixture in
// apps/api/tests/diary/voice_support.py. The recorder shim only replaces browser hardware.
export const SYNTHETIC_WEBM_BASE64 =
  "GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQRChYECGFOAZwEAAAAAAAHkEU2bdLpNu4tTq4QVSalmU6yBoU27i1OrhBZUrmtTrIHWTbuMU6uEElTDZ1OsggFMTbuMU6uEHFO7a1OsggHO7AEAAAAAAABZAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAVSalmsCrXsYMPQkBNgIxMYXZmNjMuMS4xMDJXQYxMYXZmNjMuMS4xMDJEiYhAPAAAAAAAABZUrmvxrgEAAAAAAABo14EBc8WIh9/GlPsI9H+cgQAitZyDdW5kiIEAhoZBX09QVVNWqoNjLqBWu4QExLQAg4ECI+ODhAExLQDhkZ+BAbWIQOdwAAAAAABiZIEQVe6BAGOik09wdXNIZWFkAQE4AYC7AAAAAAASVMNn13Nzn2PAgGfImUWjh0VOQ09ERVJEh4xMYXZmNjMuMS4xMDJzc7JjwItjxYiH38aU+wj0f2fIoUWjiERVUkFUSU9ORIeTMDA6MDA6MDAuMDI4MDAwMDAwAB9DtnWh54EAo4eBAACA+P/+oJOhh4EAFQD4//6bgQd1ooQAzf5gHFO7a5G7j7OBALeK94EB8YIBqPCBAw==";

/** Replace only microphone APIs while preserving a valid synthetic WebM recording. */
export async function installSyntheticWebmRecorder(page: Page): Promise<void> {
  await page.addInitScript((sampleBase64) => {
    const decoded = atob(sampleBase64);
    const sample = Uint8Array.from(decoded, (character) => character.charCodeAt(0));

    class SyntheticMediaRecorder extends EventTarget {
      static isTypeSupported(): boolean {
        return true;
      }

      state = "inactive";
      mimeType = "audio/webm;codecs=opus";

      constructor(_stream: MediaStream, _options?: MediaRecorderOptions) {
        super();
      }

      start(): void {
        this.state = "recording";
      }

      stop(): void {
        this.state = "inactive";
        this.dispatchEvent(
          new BlobEvent("dataavailable", {
            data: new Blob([sample], { type: this.mimeType }),
          }),
        );
        this.dispatchEvent(new Event("stop"));
      }

      pause(): void {
        this.state = "paused";
      }

      resume(): void {
        this.state = "recording";
      }
    }

    Object.defineProperty(window, "MediaRecorder", {
      configurable: true,
      value: SyntheticMediaRecorder,
    });
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }) },
    });
    Object.defineProperty(window, "AudioContext", {
      configurable: true,
      value: class {
        createAnalyser() {
          return { fftSize: 0, frequencyBinCount: 32, getByteFrequencyData() {} };
        }
        createMediaStreamSource() {
          return { connect() {} };
        }
        async resume() {}
        async close() {}
      },
    });
  }, SYNTHETIC_WEBM_BASE64);
}
