# DMK — Layer-0 Biometric Keystroke Dynamics

## What is this?
A keystroke dynamics experiment. Dwell and flight times for user authentication.
Hypothesis: "Password can be stolen, but rhythm cannot be copied."

## Result
Tested with 2 real users (myself + sibling). 5-6 character words ("vatan", "wsxedc").

| Metric | Value |
|--------|-------|
| FRR (false reject) | 20% |
| FAR (false accept) | 60% |

**Rejected.** At 6 characters, the system accepts everyone — including impostors.

## Why it doesn't work
1. **6 characters is insufficient signal.** 9 features, too noisy.
2. **Humans are inconsistent.** Same user writes differently each time (mood, fatigue, attention).
3. **Family members can mimic.** Sibling's rhythm was close enough to pass (FAR 60%).
4. **Threshold calibration matters more than algorithm.** Tight thresholds reject the owner; loose thresholds accept impostors.

## What's included
- `dmk_test.py` — Python test harness (dwell/flight, rollover-safe, split thresholds)
- `dmk_saha_sonuclari.json` — real user data
- C11 prototype — rejected by independent Red Team audit (8 regressions found)

## What I learned
- "Uncopiable fingerprint" is a romantic idea. Engineering reality is different.
- One hypothesis tested and disproven is worth more than ten untested claims.
- Two AIs (Gemini + GPT) debated architecture for days. The real answer came from 15 real keystrokes and a sibling.

## How to run
```bash
pip install pynput
python dmk_test.py
