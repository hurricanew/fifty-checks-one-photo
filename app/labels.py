"""Visible labels for data that is not a calibrated Decisions API result."""

SAMPLE_LABEL = "SAMPLE - not a real API result"

RECORDED_LABEL = "Recorded from one live Decisions API call"

FALLBACK_LABEL = (
    "FALLBACK: regular Gemini structured output, not the Decisions API. "
    "Confidence is not calibrated, and output tokens are billed."
)
