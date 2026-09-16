"""Clearly labelled deterministic data used only by the Phase 2 demo mode."""

DEMO_SOURCE_URL = "https://example.invalid/evidenceflow-demo"
DEMO_SOURCE_TITLE = "EvidenceFlow Fake Provider Documentation"
DEMO_QUOTE = (
    "EvidenceFlow requires every factual report claim to reference a validated "
    "evidence card containing an original source quote."
)
DEMO_PAGE_HTML = f"""
<!doctype html>
<html>
  <head><title>{DEMO_SOURCE_TITLE}</title></head>
  <body>
    <main>
      <h1>Evidence validation</h1>
      <p>{DEMO_QUOTE}</p>
      <p>This page is deterministic fake data and is not an internet source.</p>
    </main>
  </body>
</html>
""".strip()

