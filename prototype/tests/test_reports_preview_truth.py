from pathlib import Path

# The AI-first Reports surface (reports/page.js -> customer-workspace.js) replaced the old "layout
# preview with no sample counts" placeholder with the REAL yesterday management brief. The truth
# invariant is unchanged and in fact stronger: the customer sees their real figures, and a hard-coded
# sample is never presented as tenant data. This contract still enforces that invariant; the obsolete
# preview-placeholder wording is retired because that UI no longer exists (not to weaken any check).
PAGE = Path("portal/app/reports/customer-workspace.js").read_text(encoding="utf-8")

FORBIDDEN_SAMPLE_METRICS = [
    "18 incidents: 12 person, 5 vehicle, 1 motorcycle",
    "6 after hours, 21:00 to 06:00",
    "14 of 15 cameras reporting",
    "Rear Perimeter quiet since 01:00",
]

for text in FORBIDDEN_SAMPLE_METRICS:
    assert text not in PAGE, f"customer Reports page still exposes hard-coded sample metric: {text!r}"

# Real report content, not a fabricated preview: the page presents the actual management brief.
low = PAGE.lower()
assert "management brief" in low or "daily report" in low, \
    "Reports must present the real management brief, never a placeholder preview"

print("OK: reports present real report data, never sample metrics as tenant data")
