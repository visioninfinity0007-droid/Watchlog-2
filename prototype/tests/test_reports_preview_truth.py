from pathlib import Path

PAGE = "\n".join([
    Path("portal/app/reports/page.js").read_text(encoding="utf-8"),
    Path("portal/app/reports/customer-workspace.js").read_text(encoding="utf-8"),
])

FORBIDDEN_SAMPLE_METRICS = [
    "18 incidents: 12 person, 5 vehicle, 1 motorcycle",
    "6 after hours, 21:00 to 06:00",
    "14 of 15 cameras reporting",
    "Rear Perimeter quiet since 01:00",
]

for text in FORBIDDEN_SAMPLE_METRICS:
    assert text not in PAGE, f"customer Reports page still exposes hard-coded sample metric: {text!r}"

# The old layout-preview page was replaced by the real report workspace.
# Lock the current fail-closed behavior: real saved evidence or a plain
# unavailable state, never sample/demo figures presented as tenant truth.
assert "Management report" in PAGE
assert "No saved evidence report is available for this service day yet." in PAGE
assert "No report is available yet." in PAGE
assert "Snapshot evidence" in PAGE
assert "What happened, what needs attention, and whether WatchLog was watching reliably." in PAGE
assert "Layout preview — not your live figures" not in PAGE

print("OK: reports workspace cannot present sample metrics as tenant data")
