#!/usr/bin/env python3
"""Browser regression check for the standalone student canvas; no Moodle required."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def check(page):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("https://**/*", lambda route: route.abort())
    page.set_default_timeout(5000)
    page.goto(URL, wait_until="domcontentloaded")
    page.wait_for_function("window.CanvasPrototype && canvasState.result")

    # L3 and issue navigation use the production calculations.
    page.evaluate('setFactLayer("l3")')
    assert page.locator(".subnet-object").count() == 8
    page.get_by_role("button", name="Обзор слоя").click()
    assert page.locator(".overview-row").count() == 8
    page.locator(".overview-row").first.click()
    assert page.locator(".object-card h2").inner_text() == "10.0.12.0/30"
    page.get_by_role("button", name="Закрыть карточку").click()
    page.evaluate('setCanvasScenario("faults"); setFactLayer("l3")')
    messages = page.evaluate("layerIssues().map(i => i.text).join(' ')")
    assert "назначен дважды" in messages and "пересекаются" in messages
    assert "разорвана на L2" in messages
    page.get_by_role("button", name="Обзор слоя").click()
    page.locator('[data-overview-tab="issues"]').click()
    page.locator(".issue-row").first.click()
    assert page.locator(".object-card").count() == 1
    page.evaluate('closeInsight(); setCanvasScenario("base")')

    # Collection failure is distinct from both no data and interface shutdown.
    page.evaluate('canvasState.source="none"; refreshPrototypeScene(); setFactLayer("l3")')
    assert page.locator(".subnet-object").count() == 0
    assert "не получены" in page.locator("#factStatusText").inner_text()
    page.evaluate('setFactLayer("l1")')
    assert page.evaluate('canvasLinkState(links.find(l=>l.id==="l-02")).unknown')
    page.evaluate('canvasState.source="stale"; refreshPrototypeScene()')
    assert "устарел" in page.locator("#factStatusText").inner_text()
    page.evaluate('canvasState.source="error"; refreshPrototypeScene()')
    assert page.locator(".collection-error").count() > 0
    assert not page.evaluate('canvasLinkState(links.find(l=>l.id==="l-05")).down')
    page.evaluate('canvasState.source="fresh"; refreshPrototypeScene(); setFactLayer("l2"); pinInsight(nodes.find(n=>n.name==="SW1"),"ports")')

    # Empty, absent and incomplete MAC tables must not share the same message.
    page.get_by_role("button", name="Открыть MAC-таблицу").click()
    assert "02:42" in page.locator(".object-content").inner_text()
    for state, text in [("empty", "ещё не выучил"), ("missing", "не получена"), ("truncated", "неполная")]:
        page.evaluate('(state)=>{canvasState.mac=state; refreshPrototypeScene()}', state)
        assert text in page.locator(".object-content").inner_text()

    # Masked facts and topology stay closed until actual command output opens them.
    page.evaluate('closeInsight(); canvasState.mac="learned"; canvasState.mask=CanvasCore.reveal.emptyMask(); canvasState.seen=new Map(); canvasState.mode="fog"; refreshPrototypeScene(); setFactLayer("l1")')
    assert page.evaluate("visibleNodes().length") == 1
    assert page.evaluate("visibleLinks().length") == 0
    assert page.locator("#lyrDraw text").count() == 0
    page.evaluate('setFactLayer("l2")')
    assert page.evaluate("l2Model.virtuals.size") == 0
    page.evaluate('setFactLayer("l1"); pinInsight(nodes.find(n=>n.name==="R1"),"ports")')
    assert page.locator(".object-card h2").inner_text() == "Неизвестный узел"
    page.locator('[data-inspect="show ip interface brief"]').click()
    assert page.locator(".object-card h2").inner_text() == "R1"
    assert page.evaluate("visibleLinks().length") > 0
    assert not page.evaluate('canvasGroupOpen(nodes.find(n=>n.name==="R1"),"l2")')
    page.reload(wait_until="domcontentloaded")
    page.evaluate('canvasState.mode="fog"; refreshPrototypeScene()')
    assert page.evaluate("visibleLinks().length") > 0
    page.evaluate('canvasState.teacher=true; refreshPrototypeScene()')
    assert page.evaluate("visibleNodes().length") == 11
    page.evaluate('pinInsight(nodes.find(n=>n.name==="R1"),"ports")')
    assert page.locator("[data-inspect]").count() == 0

    # Seen mode keeps the physical drawing but filters facts by their revealed groups.
    page.evaluate('closeInsight(); canvasState.teacher=false; canvasState.mode="seen"; canvasState.mask=CanvasCore.reveal.emptyMask(); canvasState.seen=new Map(); refreshPrototypeScene()')
    assert page.evaluate("visibleNodes().length") == 11
    assert page.evaluate("canvasState.subnets.length") == 0
    page.evaluate('runInspection(nodes.find(n=>n.name==="PC1"),"show ip")')
    assert page.evaluate("canvasState.subnets.length") > 0

    # Independent card tabs, guest inventory, reserved L4 and mobile containment.
    page.evaluate('closeInsight(); canvasState.mode="live"; refreshPrototypeScene(); setFactLayer("l1"); pinInsight(nodes.find(n=>n.name==="R2"),"ports")')
    page.get_by_role("tab", name="L3", exact=True).click()
    assert page.evaluate("insight.layer") == "l1"
    assert "OSPF" not in page.locator(".object-content").inner_text()
    page.evaluate('pinInsight(nodes.find(n=>n.name==="PVE1"),"ports")')
    assert "Гости Proxmox" in page.locator(".object-content").inner_text()
    page.evaluate('closeInsight(); setFactLayer("l4")')
    assert "позже" in page.locator("#emptySurface").inner_text()
    for width, height in [(1440, 1000), (760, 560), (390, 844)]:
        page.set_viewport_size({"width": width, "height": height})
        page.evaluate('setFactLayer("l1"); pinInsight(nodes.find(n=>n.name==="R2"),"ports")')
        page.wait_for_timeout(400)
        assert page.evaluate("""() => { const a=wrap.getBoundingClientRect(), b=canvasCard.querySelector('foreignObject').getBoundingClientRect(); return b.left>=a.left && b.right<=a.right && b.top>=a.top && b.bottom<=a.bottom; }""")
    page.evaluate("closeInsight()")
    for zoom in [0.8, 1.4, 3, 6]:
        page.evaluate('(k)=>{view.k=k; applyView()}', zoom)
        assert abs(page.evaluate('links[0].screenGeometry.dots[0].getBoundingClientRect().width') - 6.4) < 0.01
    assert not errors, errors
    print("Canvas prototype: subnet diagnostics/navigation, collection/MAC states, discovery persistence, console facts, teacher/seen views, L4, responsive cards and fixed markers passed")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT)))
    URL = f"http://127.0.0.1:{server.server_port}/prototypes/student-ui-canvas.html"
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ.get("CHROMIUM_BIN", "/usr/bin/chromium"), args=["--no-sandbox"])
            try:
                check(browser.new_page(viewport={"width": 1440, "height": 1000}))
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
