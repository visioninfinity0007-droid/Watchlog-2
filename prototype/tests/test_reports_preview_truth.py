from pathlib import Path

PAGE = Path("portal/app/reports/page.js").read_text(encoding="utf-8")

FORBIDDEN_SAMPLE_METRICS = [
    "18 incidents: 12 person, 5 vehicle, 1 motorcycle",
    "6 after hours, 21:00 to 06:00",
    "14 of 15 cameras reporting",
    "Rear Perimeter quiet since 01:00",
]

for text in FORBIDDEN_SAMPLE_METRICS:
    assert text not in PAGE, f"customer Reports page still exposes hard-coded sample metric: {text!r}"

assert "WatchLog daily report preview" in PAGE
assert "Layout preview — not your live figures" in PAGE
assert "This preview intentionally shows no sample counts." in PAGE
assert "Your actual figures appear in sent reports and delivery history." in PAGE

print("OK: reports preview cannot present sample metrics as tenant data")
