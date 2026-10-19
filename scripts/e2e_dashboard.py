"""End-to-end check of the dashboard in a real browser (Edge via Playwright).

Expects the stack to be running: the API on :8010 with AEGIS_ADMIN_TOKEN / AEGIS_VIEWER_TOKEN set to the values
below, Redis, and `npm run dev` on :5173. Drives the UI as a person would and saves screenshots.

  python scripts/e2e_dashboard.py [output_dir]
"""
import os, re, sys, time
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
    page.locator(".deck .deck-toggle .chip").filter(has_text=re.compile("LIVE|OFFLINE|CALIBRATING")).wait_for(timeout=15000)
    time.sleep(3)   # the first status poll decides whether a fleet from an earlier session is still running
    expect(page.locator(".pill")).to_contain_text("LINK", timeout=15000)   # WebSocket connected
    stop = page.get_by_role("button", name="Stop")
    if stop.count():   # the Docker stack may still be running a fleet from an earlier session
        stop.click(); expect(page.get_by_role("button", name="Start fleet")).to_be_visible(timeout=15000); time.sleep(2)
    shot(page, "03_standby")

    print("3. start the fleet (calibration builds baselines)")
    page.get_by_role("spinbutton", name="Devices").fill("10")
    page.get_by_role("button", name="Start fleet").click()
    expect(page.locator(".boot")).to_be_visible(timeout=15000); time.sleep(8); shot(page, "04_calibrating")
    expect(page.locator(".deck-toggle .chip")).to_have_text("LIVE", timeout=180000)
    page.locator(".deck-toggle").click() if page.locator(".deck-toggle").get_attribute("aria-expanded") != "true" else None
    expect(page.get_by_text("Inject on")).to_be_visible()
    expect(page.locator(".beacon").first).to_be_visible(timeout=30000)
    time.sleep(4); shot(page, "05_live_quiet")
    n = page.locator(".beacon").count(); print("  beacons in the scene:", n); assert n == 10

    print("4. select a device from the roster and inject a restricted-zone intrusion")
    sentinel = page.get_by_label("Sentinel", exact=True)
    sentinel.get_by_role("tab", name="Roster").click()
    sentinel.locator("button.card-btn", has_text="D-003").click()
    expect(page.get_by_role("region", name="Device D-003")).to_be_visible()
    deck_toggle = page.locator(".deck-toggle")   # the deck tucks itself away once the fleet is live
    if deck_toggle.get_attribute("aria-expanded") != "true": deck_toggle.click()
    page.get_by_role("button", name=re.compile("Breach")).click()
    sentinel.get_by_role("tab", name=re.compile("Alerts")).click()
    # The Docker stack keeps its data between runs, so older D-003 alerts may be listed: target the OPEN one.
    open_alert = sentinel.locator("button.card-btn").filter(has_text="D-003").filter(has_text="Open")
    expect(open_alert.first).to_be_visible(timeout=90000)
    expect(open_alert.first).to_contain_text("entered a restricted zone")
    time.sleep(4); shot(page, "06_alert_raised")
    expect(page.get_by_text("Why it was flagged")).to_be_visible()
    assert page.locator(".beacon .ping").count() >= 1, "a high-severity device should ping in the scene"
    detail = page.get_by_role("region", name="Device D-003").inner_text()
    assert "not a probability" in detail and "restricted zone" in detail

    print("5. restore normal; the alert should close")
    page.get_by_role("button", name="Restore normal").click()
    expect(open_alert).to_have_count(0, timeout=90000)  # that alert closes...
    expect(sentinel.locator("button.card-btn").filter(has_text="D-003").filter(has_text="Closed").first).to_be_visible()
    shot(page, "07_alert_closed")

    print("6. command palette, archive and findings")
    page.keyboard.press("/"); expect(page.get_by_role("dialog", name="Command palette")).to_be_visible()
    page.keyboard.type("D-005"); page.keyboard.press("Enter")
    expect(page.get_by_role("region", name="Device D-005")).to_be_visible()
    page.get_by_role("button", name="Archive").click(); time.sleep(2); shot(page, "08_archive")
    page.get_by_role("button", name="Show data as tables").click(); expect(page.locator("table.data").first).to_be_visible(); shot(page, "09_archive_tables")
    page.get_by_role("button", name="Findings").click(); expect(page.get_by_text("Pre-registered comparisons")).to_be_visible(); shot(page, "10_findings")
    page.get_by_role("button", name="Observe").click()

    print("7. reload keeps the session; sign out returns to login")
    page.reload(); expect(page.get_by_role("button", name="Sign out")).to_be_visible(timeout=15000)
    page.get_by_role("button", name="Sign out").click(); expect(page.get_by_label("Access token")).to_be_visible()

    print("8. viewer sees the data but no controls")
    page.get_by_label("Access token").fill(VIEWER); page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_text("Viewers can watch but not control")).to_be_visible(timeout=15000)
    expect(page.get_by_role("button", name="Start fleet")).to_have_count(0)
    shot(page, "11_viewer")

    print("9. phone-sized screen")
    ph = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, color_scheme="dark")
    mp = ph.new_page(); mp.goto(BASE)
    mp.get_by_label("Access token").fill(VIEWER); mp.get_by_role("button", name="Sign in").click()
    expect(mp.locator(".beacon").first).to_be_visible(timeout=30000); time.sleep(2)
    mp.screenshot(path=str(OUT / "12_phone.png"), full_page=True); print("  screenshot: 12_phone")
    overflow = mp.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    print("  horizontal overflow (px):", overflow); assert overflow <= 1
    ph.close()

    ctx.close(); browser.close()
    unexpected = [b for b in bad_responses if not (b.startswith("401") and b.endswith("/api/me")) and "favicon" not in b]
    print("failed HTTP responses (excluding the deliberate bad login):", unexpected if unexpected else "none")
    print("browser errors:", errors if errors else "none")
    assert not errors and not unexpected, (errors, unexpected)
print("E2E OK")
