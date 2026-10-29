"""The demo as an executable story: one normal device, one anomaly, one outage, one attack in the blind spot, one recovery.

Drives the real dashboard in a browser against a running stack and saves captioned screenshots to docs/img/demo/. It asserts what each
scene must show, so the story cannot drift away from what the system actually does.

  AEGIS_UI=http://localhost:8080 AEGIS_ADMIN_TOKEN=... python scripts/demo_story.py
"""

import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("AEGIS_UI", "http://localhost:8080")
ADMIN = os.environ.get("AEGIS_ADMIN_TOKEN") or next((ln.split("=", 1)[1].strip() for ln in Path(".env").read_text().splitlines() if ln.startswith("AEGIS_ADMIN_TOKEN=")), "")
OUT = Path("docs/img/demo")
OUT.mkdir(parents=True, exist_ok=True)
NORMAL, ATTACKED, BLIND = "D-005", "D-003", "D-006"  # three different devices so each scene starts from a clean baseline


def nav(page, name):
    page.get_by_role("navigation", name="Sections").get_by_role("button", name=re.compile(name)).click()
    time.sleep(0.7)


def shot(page, name, caption):
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=False)
    print(f"  [{name}] {caption}")


def select(page, device):
    page.get_by_role("button", name=re.compile(rf"^{device},")).click()
    expect(page.get_by_label("Cross-modal correlation")).to_contain_text(device)


def scenario(page, device, label_re):
    nav(page, "Scenarios")
    page.get_by_label("Target device").select_option(device)
    page.get_by_role("button", name=re.compile(label_re)).click()
    expect(page.get_by_role("status").filter(has_text=re.compile("started on|Restored"))).to_be_visible(timeout=15000)
    page.get_by_role("button", name=re.compile("Watch it on the map")).click()


with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    page = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark").new_page()
    page.goto(BASE)
    page.get_by_label("Access token").fill(ADMIN)
    page.get_by_role("button", name="Sign in").click()
    expect(page.locator(".pill").last).to_contain_text("LINK", timeout=15000)
    time.sleep(4)

    print("0. a live fleet (starts one if needed)")
    nav(page, "Scenarios")
    if page.get_by_role("button", name="Start fleet").count():
        page.get_by_role("spinbutton", name="Devices").fill("10")
        page.get_by_role("button", name="Start fleet").click()
    nav(page, "Overview")
    expect(page.locator(".beacon").first).to_be_visible(timeout=240000)
    time.sleep(6)

    print("1. a normal device: everything it can sense is ordinary")
    select(page, NORMAL)
    time.sleep(2)
    assert "Normal" in page.locator(".focus-chip").inner_text(), page.locator(".focus-chip").inner_text()
    shot(page, "01_normal", f"{NORMAL}: all four signals seen, score low. Normal is a statement about four signals, not a guess.")

    print("2. a device becomes anomalous: it enters the server room")
    scenario(page, ATTACKED, "Breach")
    card = page.get_by_label("Live anomalies").locator(".acard").filter(has_text=ATTACKED)
    expect(card.first).to_be_visible(timeout=120000)
    time.sleep(5)
    shot(page, "02_anomalous", f"{ATTACKED}: location signal fires, the alert opens with its reason; the correlation lanes show which signals moved.")
    nav(page, "Scenarios"); page.get_by_label("Target device").select_option(ATTACKED); page.get_by_role("button", name="Restore normal").click()
    nav(page, "Overview")
    expect(page.get_by_label("Live anomalies").locator(".acard").filter(has_text=ATTACKED)).to_have_count(0, timeout=120000)

    print("3. a signal outage: the network signal of one device stops reporting")
    scenario(page, BLIND, "Lose network signal")
    blind = page.get_by_label("Devices that are not fully observed")
    expect(blind).to_contain_text(BLIND, timeout=60000)
    time.sleep(3)
    assert "Degraded" in page.locator(".focus-chip").inner_text() and "Normal" not in page.locator(".focus-chip").inner_text()
    shot(page, "03_outage", f"{BLIND}: not an anomaly and no alert, but never 'Normal'. Network is unavailable; the score now rests on three signals.")

    print("4. an attack inside the blind spot: exfiltration while the network signal is gone")
    nav(page, "Scenarios")
    page.get_by_label("Target device").select_option(BLIND)
    page.get_by_role("button", name=re.compile("Exfiltrate")).click()
    expect(page.get_by_role("status").filter(has_text="started on")).to_be_visible(timeout=15000)
    page.get_by_role("button", name=re.compile("Watch it on the map")).click()
    time.sleep(6)
    n_alerts = page.get_by_label("Open alerts").locator(".acard").filter(has_text=BLIND).count()
    shot(page, "04_blind_spot", f"{BLIND}: the exfiltration is real and the system cannot see it. No alert (open alerts for this device: {n_alerts}); it says Degraded, not healthy.")

    print("5. the outage ends; the window refills; the device is still Degraded for a minute of simulated time")
    # The outage lasts 240 simulated seconds (12 s at 20x). The device says "recovering" from the moment the signal returns until
    # its one-minute window has refilled (3 s at 20x), so poll quickly to catch it.
    deadline = time.time() + 90
    saw_recovery = False
    while time.time() < deadline:
        txt = page.get_by_label("Devices that are not fully observed").inner_text() if page.get_by_label("Devices that are not fully observed").count() else ""
        if "recovering" in txt:
            saw_recovery = True
            break
        time.sleep(0.15)
    assert saw_recovery, "never saw the recovery state"
    shot(page, "05_recovering", f"{BLIND}: the signal is back but its one-minute window is refilling, so the detector withholds judgment. Honest, not instant.")

    expect(page.get_by_label("Live anomalies")).to_contain_text("fully observed again", timeout=60000)
    print("6. after the window refills the exfiltration is finally seen, and the alert opens")
    card = page.get_by_label("Live anomalies").locator(".acard").filter(has_text=BLIND)
    expect(card.first).to_be_visible(timeout=120000)
    time.sleep(4)
    shot(page, "06_alert_after_recovery", f"{BLIND}: the network signal is observed again and the exfiltration raises an alert, late: the blind period is not recoverable.")
    nav(page, "Scenarios"); page.get_by_label("Target device").select_option(BLIND); page.get_by_role("button", name="Restore normal").click()
    nav(page, "Overview")
    expect(page.get_by_label("Devices that are not fully observed")).to_have_count(0, timeout=120000)
    time.sleep(3)
    shot(page, "07_fully_observed", "Everything restored: no blind spots, no open alerts.")
    browser.close()
print("DEMO STORY OK")
