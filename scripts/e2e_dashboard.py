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
def nav(page, name): page.get_by_role("navigation", name="Sections").get_by_role("button", name=re.compile(name)).click(); time.sleep(0.6)

with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark")
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

    print("2. admin login; make sure no fleet is left over from an earlier session")
    page.get_by_label("Access token").fill(ADMIN); page.get_by_role("button", name="Sign in").click()
    expect(page.locator(".pill").last).to_contain_text("LINK", timeout=15000)   # WebSocket connected
    time.sleep(4)   # the first status poll tells us whether a fleet is still running
    nav(page, "Scenarios")
    stop = page.get_by_role("button", name="Stop", exact=True)
    if stop.count():
        stop.click(); expect(page.get_by_role("button", name="Start fleet")).to_be_visible(timeout=15000); time.sleep(2)
    nav(page, "Overview"); shot(page, "03_standby")

    print("3. start the fleet from Scenarios; Overview shows calibration, then the live map")
    nav(page, "Scenarios"); shot(page, "03b_scenarios_idle")
    page.get_by_role("spinbutton", name="Devices").fill("10")
    page.get_by_role("button", name="Start fleet").click()
    nav(page, "Overview")
    expect(page.locator(".boot")).to_be_visible(timeout=15000); time.sleep(8); shot(page, "04_calibrating")
    expect(page.locator(".beacon").first).to_be_visible(timeout=180000)
    time.sleep(6); shot(page, "05_live_quiet")
    n = page.locator(".beacon").count(); print("  beacons in the scene:", n); assert n == 10
    expect(page.get_by_label("Cross-modal correlation")).to_contain_text("D-0")   # defaults to the riskiest device

    print("4. select D-003 on the map, inject a restricted-zone intrusion from Scenarios")
    page.get_by_role("button", name=re.compile(r"^D-003,")).click()
    expect(page.get_by_label("Cross-modal correlation")).to_contain_text("D-003")
    nav(page, "Scenarios")
    expect(page.get_by_label("Target device")).to_have_value("D-003")
    page.get_by_role("button", name=re.compile("Breach")).click()
    expect(page.get_by_role("status").filter(has_text="started on D-003")).to_be_visible(timeout=15000); shot(page, "06a_injected")
    page.get_by_role("button", name=re.compile("Watch it on the map")).click()
    # The Docker stack keeps its data between runs, so older D-003 alerts may exist: target the OPEN one in the feed.
    open_alert = page.get_by_label("Live anomalies").locator(".acard").filter(has_text="D-003")
    expect(open_alert.first).to_be_visible(timeout=90000)
    expect(open_alert.first).to_contain_text("entered a restricted zone")
    time.sleep(6); shot(page, "06_alert_raised")
    assert page.locator(".beacon .ping").count() >= 1, "a high-severity device should ping in the scene"
    corr = page.get_by_label("Cross-modal correlation")
    expect(corr).to_contain_text("Why it was flagged")
    text = corr.inner_text()
    assert "not a probability" in text and "restricted zone" in text, text

    print("5. restore normal; the alert should close")
    nav(page, "Scenarios"); page.get_by_role("button", name="Restore normal").click()
    nav(page, "Overview")
    expect(open_alert).to_have_count(0, timeout=90000)
    shot(page, "07_alert_closed")

    print("6. device network, anomalies, command palette, statistics, evidence")
    nav(page, "Device Network"); expect(page.locator("table.devices tbody tr")).to_have_count(10); shot(page, "08_network")
    nav(page, "Anomalies"); expect(page.get_by_text("Signals that combined")).to_be_visible(); shot(page, "09_anomalies")
    page.keyboard.press("/"); expect(page.get_by_role("dialog", name="Command palette")).to_be_visible()
    page.keyboard.type("D-005"); page.keyboard.press("Enter")
    expect(page.locator(".focus-chip")).to_contain_text("D-005")
    nav(page, "Statistics"); time.sleep(2); shot(page, "10_statistics")
    page.get_by_role("button", name="Show data as tables").click(); expect(page.locator("table.data").first).to_be_visible()
    nav(page, "Evidence"); expect(page.get_by_text("Pre-registered comparisons")).to_be_visible(); shot(page, "11_evidence")
    nav(page, "Overview")

    print("7. reload keeps the session; sign out returns to login")
    page.reload(); expect(page.get_by_role("button", name="Sign out")).to_be_visible(timeout=15000)
    page.get_by_role("button", name="Sign out").click(); expect(page.get_by_label("Access token")).to_be_visible()

    print("8. viewer sees the data but no controls")
    page.get_by_label("Access token").fill(VIEWER); page.get_by_role("button", name="Sign in").click()
    expect(page.locator(".beacon").first).to_be_visible(timeout=30000)
    nav(page, "Scenarios")
    expect(page.get_by_text("Viewers can watch but not control")).to_be_visible(timeout=15000)
    expect(page.get_by_role("button", name="Start fleet")).to_have_count(0)
    shot(page, "12_viewer")

    print("9. phone-sized screen")
    ph = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, color_scheme="dark")
    mp = ph.new_page(); mp.goto(BASE)
    mp.get_by_label("Access token").fill(VIEWER); mp.get_by_role("button", name="Sign in").click()
    expect(mp.locator(".beacon").first).to_be_visible(timeout=30000); time.sleep(3)
    mp.screenshot(path=str(OUT / "13_phone.png"), full_page=True); print("  screenshot: 13_phone")
    overflow = mp.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    print("  horizontal overflow (px):", overflow); assert overflow <= 1
    ph.close()

    ctx.close(); browser.close()
    unexpected = [b for b in bad_responses if not (b.startswith("401") and b.endswith("/api/me")) and "favicon" not in b]
    print("failed HTTP responses (excluding the deliberate bad login):", unexpected if unexpected else "none")
    print("browser errors:", errors if errors else "none")
    assert not errors and not unexpected, (errors, unexpected)
print("E2E OK")
