# R18 style library and Director

Mindspace provides an optional product-level R18 mode.  It is deliberately
separate from every character card:

- `r18_director` is the single global R18 library.  It combines the reusable
  Director, the selected style pack and the read-only encrypted DOCX extension,
  then decides the session-local phase, continuation evidence and minimum
  single-pass intensity.
- `r18_style_id` selects one compact style pack: `high_intensity`,
  `immersive_narrative`, or `dialogue_led`.
- Character cards provide only the character's voice, motives, vocabulary and
  boundaries.  They may supply one secondary local overlay, but never own the
  global R18 protocol or the encrypted DOCX source.

The Director loads only the modules needed for the current phase plus at most
two short style examples.  The desktop product can additionally carry one
encrypted, read-only DOCX source resource.  It is decrypted in Core memory
only while R18 is enabled and contributes at most one rotating private overlay
per turn; no API, profile editor, RAG index, memory writer or diagnostics
endpoint exposes its contents.  A legacy user-owned `roleplay.r18_protocol`
remains a local supplemental overlay.

The resource is light at-rest protection against casual file browsing, not a
password vault against someone with control of the installed application.  The
packaging script intentionally accepts an external local DOCX path and writes
only its encrypted binary into the product build; the plaintext document is
never copied into this repository or update payload as a standalone file.

Scene state is derived independently for each session from the visible local
turn history.  Explicit continuation may advance the scene; explicit stop or
topic shift pauses it.  Initiative runs and silence are never interpreted as
permission to escalate.

R18 uses a six-level ordered ladder: explicit request, active seduction, direct
sexual language, dirty talk with an actual sex act, high-intensity
vocalization, and praise/climax feedback.  Recent stalled replies and persisted
quality reasons choose the minimum level for the next prompt. Rephrasing “wait,
tell me first, I will think about it” cannot reset the ladder.

The ladder is part of the single primary generation prompt. Quality evaluation
records drift for diagnostics and the next turn, but never starts a second
foreground model call. This preserves normal response latency while making
repeated delay raise the next turn's minimum intensity.
