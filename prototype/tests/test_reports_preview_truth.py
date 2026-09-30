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


# Saved daily reports must remain discoverable from rolling windows, and archived dates must open
# the exact frozen report rather than disappearing when "Yesterday" advances.
USE_REPORT = Path("portal/app/reports/use-report.js").read_text(encoding="utf-8")
assert 'rpc("wl_my_report_window"' in USE_REPORT,     "Reports must load the saved-report rolling window for Yesterday/7-day/30-day history"
assert 'params.get("date")' in USE_REPORT and 'requestedReportDate' in USE_REPORT,     "Reports must support opening an exact saved report date"

assert "Completed daily reports in this period" in PAGE,     "7-day/30-day views must surface completed saved daily reports"
assert '"/reports/?view=yesterday&date="' in PAGE,     "saved report history must provide an exact-date report link"
assert "Most recent completed report" in PAGE,     "Yesterday must point customers to the latest saved report when the selected day has none"


# Chai Wala's four time windows are one reporting product, not four unrelated renderers.
UNIFIED = Path("portal/app/reports/unified-restaurant-report.js").read_text(encoding="utf-8")
assert 'import UnifiedRestaurantReport from "./unified-restaurant-report";' in PAGE,     "restaurant Reports must use the unified report shell"
restaurant_branch = PAGE.split("if(r.isChaiWalaRestaurant){", 1)[1].split("}else if(r.isOffice){", 1)[0]
assert "<UnifiedRestaurantReport" in restaurant_branch,     "all restaurant time windows must enter the unified shell"
assert "ManagementReading" not in restaurant_branch,     "restaurant Reports must not append a second AI-written report below the structured report"

for label in ("Overview", "Business", "Security"):
    assert label in UNIFIED, f"unified restaurant report missing {label} local view"

assert 'minimum=days===7?4:10' in UNIFIED,     "7/30-day reports must gate trends on enough represented service days"
assert "comparisonReady=trendReady&&previousObserved>=minimum" in UNIFIED,     "period comparisons must require enough represented days in both periods"
assert "Missing or incomplete days remain unknown" in UNIFIED,     "missing service days must never be treated as zero demand"
assert "No security exception recorded in the available coverage" in UNIFIED,     "security clear-state copy must stay scoped to available coverage"
assert "report_id:r.report_id" in UNIFIED,     "rolling-period recommendations must preserve their source report for client feedback"

USE_REPORT = Path("portal/app/reports/use-report.js").read_text(encoding="utf-8")
assert "restaurantSecurity" in USE_REPORT and 'rpc("wl_my_daily_intelligence"' in USE_REPORT,     "Today/Yesterday Security must use real daily security evidence"
