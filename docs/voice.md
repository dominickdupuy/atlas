# Voice (phase 6) — setup notes

No code in this repo implements voice, by design (D3): the entire pipeline is
Home Assistant Assist over Wyoming. This file records what to do when the
microphone hardware arrives, so phase 6 is configuration, not archaeology.

## Hardware

Home Assistant Voice Preview Edition (Appendix A). Wake word runs on its
ESP32; it joins HA as an Assist satellite with no driver work. The Beats Pill
connects to the satellite's 3.5mm jack — output only, never as a microphone
(Appendix A explains why Bluetooth capture is rejected).

## Services

Uncomment the `voice` profile in `compose.yaml` and pin the then-current
Wyoming images (D19 pinning rules apply):

- `rhasspy/wyoming-speech-to-phrase` — local constrained-grammar STT (D10);
  resolves known commands in ~150ms.
- `rhasspy/wyoming-whisper` (faster-whisper `base.en`) — the local fallback
  for open-ended utterances if cloud STT is undesirable.
- `rhasspy/wyoming-piper` — TTS.

Memory check before enabling (D1's budget): local STT + kiosk + HA must fit.

## Assist pipeline configuration (in HA)

1. Add each Wyoming service (Settings → Devices & Services → Wyoming) by
   compose service name and port.
2. Create the pipeline: wake word on-satellite → Speech-to-Phrase →
   `prefer_local_intents: true` (D9) → fallback conversation agent.
3. Point the conversation agent at the same MCP tool set the job runner uses
   (D4) — HA's MCP Client integration.
4. Entity exposure: expose only what voice should touch; sensitive entities
   stay out with never-control rules (section 8).
5. Destructive intents require confirmation before execution (section 8), and
   voice is never an approval channel for consequential actions (D8).

## Open at phase 6

- Cloud STT provider for open-ended utterances (spec §11) — Anthropic does
  not offer STT, so this is a separate choice.
- Display states `LISTENING` / `THINKING` / `SPEAKING`: the runner already
  renders them; wire HA's Assist pipeline events to MQTT so the board reacts
  (`atlas/display/mode`).
