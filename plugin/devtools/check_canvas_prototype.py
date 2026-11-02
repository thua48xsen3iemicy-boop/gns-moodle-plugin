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

    # Closing details keeps the chosen device and its connections selected.
    page.locator('#lyrNodes .node').filter(has=page.locator('.nname', has_text="SW1")).locator('.ico').click()
    assert page.evaluate("visibleNodes().filter(n=>n.g.classList.contains('connection-related')).map(n=>n.name).sort()") == ["PC1", "PC2", "R2", "SW1"]
    assert page.locator("#lyrLinks > .connection-related").count() == 3
    assert page.evaluate("nodes.find(n=>n.name==='R3').g.classList.contains('connection-dim')")
    assert page.evaluate("""() => { const card=canvasCard.querySelector('foreignObject').getBoundingClientRect(); return [...document.querySelectorAll('#lyrNodes .connection-related .ico')].every(g=>{const r=g.getBoundingClientRect();return r.right<=card.left || r.left>=card.right || r.bottom<=card.top || r.top>=card.bottom;}); }""")
    fault_colour = page.locator("#lyrLinks .link.down .physical-wire").first.evaluate("g=>getComputedStyle(g).stroke")
    page.evaluate('pinInsight(nodes.find(n=>n.name==="SW2"),"ports")')
    assert page.locator("#lyrLinks .link.down .physical-wire").first.evaluate("g=>getComputedStyle(g).stroke") == fault_colour
    page.get_by_role("button", name="Закрыть карточку").click()
    assert page.locator(".object-card").count() == 0
    assert page.locator("#lyrLinks > .connection-related").count() == 4
    page.evaluate("refreshPrototypeScene()")
    assert page.locator(".object-card").count() == 0
    assert page.locator("#lyrLinks > .connection-related").count() == 4
    page.locator('#bg').click(position={"x": 8, "y": 250})
    assert page.locator("#vp .connection-related,#vp .connection-dim").count() == 0

    # L3 selection marks the subnet and every participant, including all member edges.
    page.evaluate('setFactLayer("l3")')
    page.locator('[data-subnet="10.1.10.0/24"] rect').click()
    assert page.evaluate("visibleNodes().filter(n=>n.g.classList.contains('connection-related')).map(n=>n.name).sort()") == ["PC1", "PC2", "R2"]
    assert page.locator(".subnet-object.connection-primary").count() == 1
    assert page.locator(".subnet-edge.connection-related").count() == 3
    assert page.locator("#lyrL3 .auto-label.connection-related").count() == 3
    page.get_by_role("button", name="Закрыть карточку").click()
    assert page.locator(".object-card").count() == 0
    assert page.locator(".subnet-object.connection-primary").count() == 1
    page.evaluate("refreshPrototypeScene()")
    assert page.locator(".subnet-edge.connection-related").count() == 3
    assert page.locator(".object-card").count() == 0
    page.locator('[data-subnet="10.0.12.0/30"] rect').click()
    assert page.locator(".subnet-object.connection-primary").count() == 1
    assert page.locator(".subnet-edge.connection-related").count() == 2
    assert page.locator('[data-subnet="10.1.10.0/24"]').evaluate("g=>g.classList.contains('connection-dim') && !g.classList.contains('sel')")
    page.locator("#svg").press("Escape")
    assert page.locator("#vp .connection-related,#vp .connection-dim,.subnet-object.sel").count() == 0

    # Node selection on L3 traces its shared subnets, and survives changing its card tab.
    page.evaluate('setFactLayer("l1"); pinInsight(nodes.find(n=>n.name==="PC1"),"ports"); setFactLayer("l3")')
    assert page.locator(".subnet-object.connection-related").count() == 1
    assert page.evaluate("visibleNodes().filter(n=>n.g.classList.contains('connection-related')).map(n=>n.name).sort()") == ["PC1", "PC2", "R2"]
    page.get_by_role("tab", name="L2", exact=True).click()
    assert page.locator(".subnet-edge.connection-related").count() == 3
    page.get_by_role("button", name="Закрыть карточку").click()
    assert page.locator(".subnet-edge.connection-related").count() == 3

    # VLAN selection stays within its L2 domain rather than highlighting the whole chassis.
    page.evaluate('setFactLayer("l2")')
    page.locator('.l2-virtual').filter(has=page.locator('.vtitle', has_text="VLAN 10")).click()
    assert page.evaluate("[...l2Model.virtuals.values()].filter(v=>v.g.classList.contains('connection-related')).map(v=>v.vlan)") == [10]
    assert page.evaluate("l2Model.segments.filter(s=>s.g.classList.contains('connection-related')).every(s=>s.vlan===10)")
    page.get_by_role("button", name="Закрыть карточку").click()
    assert page.locator(".l2-virtual.connection-primary").count() == 1
    page.locator('#bg').click(position={"x": 8, "y": 250})
    assert page.locator("#vp .connection-related,#vp .connection-dim,.l2-virtual.sel").count() == 0

    # Escape from inside an open card remains an explicit selection reset.
    page.evaluate('setFactLayer("l1"); pinInsight(nodes.find(n=>n.name==="SW1"),"ports")')
    page.get_by_role("tab", name="L1", exact=True).press("Escape")
    assert page.locator(".object-card,#vp .connection-related,#vp .connection-primary,.node.sel").count() == 0

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
    page.evaluate('pinInsight(nodes.find(n=>n.name==="R1"),"ports")')
    assert page.locator("#lyrNodes .connection-related").count() == 1
    assert page.locator("#lyrLinks .connection-related").count() == 0
    page.evaluate("closeInsight()")
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
    # Adaptive contours stay thin at overview zoom, with a bounded screen width up close.
    page.evaluate('pinInsight(nodes.find(n=>n.name==="R2"),"ports"); hideInsight()')
    widths = []
    for zoom in [0.22, 0.4, 0.8, 1.4, 3, 6]:
        page.evaluate('(k)=>{view.k=k; applyView()}', zoom)
        assert abs(page.evaluate('links[0].screenGeometry.dots[0].getBoundingClientRect().width') - 6.4) < 0.01
        ring = page.locator('#lyrNodes .connection-primary .halo')
        assert ring.evaluate("g=>getComputedStyle(g).vectorEffect") == "non-scaling-stroke"
        widths.append(float(ring.evaluate("g=>getComputedStyle(g).strokeWidth").removesuffix("px")))
    assert widths[0] <= 0.7 and max(widths) <= 1.5 and widths[0] < widths[-1]
    assert widths == sorted(widths)
    page.evaluate("closeInsight()")
    hover_widths = []
    for zoom in [0.4, 3]:
        page.evaluate('(k)=>{const n=nodes.find(n=>n.name==="R2");Object.assign(view,{k,tx:wrap.clientWidth/2-k*n.cx,ty:wrap.clientHeight/2-k*n.cy});applyView()}', zoom)
        hovered = page.locator('#lyrNodes .node').filter(has=page.locator('.nname', has_text="R2"))
        hovered.locator('.ico').hover()
        assert hovered.evaluate("g=>g.matches(':hover')")
        hover_widths.append(float(hovered.locator('.halo').evaluate("g=>getComputedStyle(g).strokeWidth").removesuffix("px")))
    assert hover_widths[0] <= 0.5 and hover_widths[-1] <= 1.1
    page.evaluate('closeInsight(); setFactLayer("l3"); pinInsight(canvasState.subnets[0],"ports"); hideInsight()')
    subnet_border = page.locator('.subnet-object.connection-primary rect')
    assert float(subnet_border.evaluate("g=>getComputedStyle(g).strokeWidth").removesuffix("px")) <= 1.5
    assert not errors, errors
    print("Canvas prototype: connection/subnet selection, diagnostics/navigation, collection/MAC states, discovery persistence, console facts, teacher/seen views, L4, responsive cards and fixed markers passed")


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
