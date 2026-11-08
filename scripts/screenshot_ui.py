"""Screenshot the running UI in a real browser (Playwright driving the installed Edge) into proof_results/.

    .venv\\Scripts\\python.exe scripts/screenshot_ui.py [--base http://127.0.0.1:8765]
"""
import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:8765")
args = ap.parse_args()
out = ROOT / "proof_results"

with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge")
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(args.base + "/", wait_until="networkidle")
    page.wait_for_selector(".ag-row", timeout=15000)
    page.locator(".ag-row").first.click()
    page.wait_for_timeout(1500)
    page.screenshot(path=str(out / "ui_case.png"), full_page=True)
    page.get_by_role("button", name="Policy").first.click()
    page.wait_for_timeout(800)
    page.screenshot(path=str(out / "ui_policy.png"), full_page=True)
    page.get_by_text("Dispute queue").first.click()
    page.get_by_text("Results board").first.click()
    page.wait_for_selector("#boardGrid .ag-row", timeout=15000)
    page.wait_for_timeout(800)
    page.screenshot(path=str(out / "ui_board.png"), full_page=True)
    phone = browser.new_page(viewport={"width": 390, "height": 844})
    phone.goto(args.base + "/", wait_until="networkidle")
    phone.wait_for_timeout(1500)
    phone.screenshot(path=str(out / "ui_phone.png"), full_page=True)
    print("page errors:", errors or "none")
    browser.close()
print("wrote proof_results/ui_case.png, ui_policy.png, ui_board.png and ui_phone.png")
