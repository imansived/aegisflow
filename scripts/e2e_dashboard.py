"""End-to-end check of the dashboard in a real browser (Edge via Playwright).

Expects the stack to be running: the API on :8010 with AEGIS_ADMIN_TOKEN / AEGIS_VIEWER_TOKEN set to the values
below, Redis, and `npm run dev` on :5173. Drives the UI as a person would and saves screenshots.

  python scripts/e2e_dashboard.py [output_dir]
"""
import os, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

# Defaults match the local dev setup; point AEGIS_UI at http://localhost:8080 and export the real tokens
# to run the same script against the Docker stack.
BASE = os.environ.get("AEGIS_UI", "http://localhost:5173")
ADMIN = os.environ.get("AEGIS_ADMIN_TOKEN", "admin-demo-token-0123456789")
VIEWER = os.environ.get("AEGIS_VIEWER_TOKEN", "viewer-demo-token-0123456789")
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "e2e_shots"); OUT.mkdir(parents=True, exist_ok=True)

def shot(page, name): page.screenshot(path=str(OUT / f"{name}.png"), full_page=True); print("  screenshot:", name)

with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, color_scheme="dark")
    page = ctx.new_page()
    errors, bad_responses = [], []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" and "Failed to load resource" not in m.text else None)
    # Server errors are reported WITH their URL. The one expected failure is the deliberate bad-token login.
    page.on("response", lambda r: bad_responses.append(f"{r.status} {r.request.method} {r.url}") if r.status >= 400 else None)

    print("1. login screen, bad token")
    page.goto(BASE); shot(page, "01_login")
    page.get_by_label("Access token").fill("definitely-not-a-token"); page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("alert")).to_contain_text("not accepted"); shot(page, "02_login_error")

    print("2. admin login")
    page.get_by_label("Access token").fill(ADMIN); page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("button", name="Start fleet")).to_be_visible(timeout=15000)
    expect(page.get_by_role("status")).to_contain_text("Live", timeout=15000)   # WebSocket connected
    shot(page, "03_empty_console")

    print("3. start the fleet (warm-up builds baselines)")
    page.get_by_role("spinbutton", name="Devices").fill("10")
    page.get_by_role("button", name="Start fleet").click()
    expect(page.get_by_role("progressbar")).to_be_visible(timeout=15000); time.sleep(8); shot(page, "04_warming")
    expect(page.get_by_text("Inject on")).to_be_visible(timeout=180000)
    expect(page.locator(".marker").first).to_be_visible(timeout=30000)
    time.sleep(4); shot(page, "05_live_quiet")
    n_markers = page.locator(".marker").count(); print("  device markers on the map:", n_markers); assert n_markers == 10

    print("4. select a device from the list and inject a restricted-zone intrusion")
    page.locator("section[aria-label='Devices'] button.row", has_text="D-003").click()
    expect(page.get_by_role("region", name="Device D-003")).to_be_visible()
    page.get_by_role("button", name="Enter restricted zone").click()
    # The Docker stack keeps its data between runs, so older D-003 alerts may be listed: target the OPEN one.
    feed_rows = page.locator("section[aria-label='Alerts and activity'] button.row")
    open_alert = feed_rows.filter(has_text="D-003").filter(has_text="Open")
    expect(open_alert.first).to_be_visible(timeout=90000)
    expect(open_alert.first).to_contain_text("entered a restricted zone")
    time.sleep(3); shot(page, "06_alert_raised")
    expect(page.get_by_text("Why it was flagged")).to_be_visible()
    assert page.locator(".marker.pulse").count() >= 1, "a high-severity device should pulse on the map"
    detail = page.get_by_role("region", name="Device D-003").inner_text()
    assert "not a probability" in detail and "restricted zone" in detail

    print("5. restore normal; the alert should close")
    page.get_by_role("button", name="Restore normal").click()
    expect(open_alert).to_have_count(0, timeout=90000)  # that alert closes...
    expect(feed_rows.filter(has_text="D-003").filter(has_text="Closed").first).to_be_visible()  # ...and is listed as closed
    shot(page, "07_alert_closed")

    print("6. analytics and evidence pages, light theme")
    page.get_by_role("button", name="Analytics").click(); time.sleep(2); shot(page, "08_analytics")
    page.get_by_role("button", name="Show data as tables").click(); expect(page.locator("table.data").first).to_be_visible(); shot(page, "09_analytics_tables")
    page.get_by_role("button", name="Evidence").click(); expect(page.get_by_text("Pre-registered comparisons")).to_be_visible(); shot(page, "10_evidence")
    page.get_by_role("button", name="Live").click(); page.get_by_role("button", name="Toggle light or dark theme").click(); time.sleep(1); shot(page, "11_live_light")

    print("7. reload keeps the session; sign out returns to login")
    page.reload(); expect(page.get_by_role("button", name="Sign out")).to_be_visible(timeout=15000)
    page.get_by_role("button", name="Sign out").click(); expect(page.get_by_label("Access token")).to_be_visible()

    print("8. viewer sees the data but no controls")
    page.get_by_label("Access token").fill(VIEWER); page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_text("Viewers can watch but not control")).to_be_visible(timeout=15000)
    expect(page.get_by_role("button", name="Start fleet")).to_have_count(0)
    shot(page, "12_viewer")

    ctx.close(); browser.close()
    unexpected = [b for b in bad_responses if not (b.startswith("401") and b.endswith("/api/me")) and "favicon" not in b]
    print("failed HTTP responses (excluding the deliberate bad login):", unexpected if unexpected else "none")
    print("browser errors:", errors if errors else "none")
    assert not errors and not unexpected, (errors, unexpected)
print("E2E OK")
