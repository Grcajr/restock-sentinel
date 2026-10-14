"""One-off diagnostic script — NOT part of the project, safe to delete after use.

Reads the product title straight from the live page via Playwright's own JS
evaluation (bypassing BeautifulSoup entirely) so we can see whether the
accented-character corruption already exists at the browser/DOM level, or
gets introduced somewhere later in our own parsing code.
"""

from playwright.sync_api import sync_playwright

URL = "https://www.amazon.com.mx/Pok-mon-Tcg-Evolution-Trainer/dp/B0GYVHLP4L?ref_=ast_bl_cpl_dp"

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(locale="es-MX")
    page.goto(URL, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        pass

    title = page.eval_on_selector("#productTitle", "el => el ? el.textContent : null")
    print("Title via Playwright JS (DOM level):")
    print(repr(title))
    if title:
        non_ascii = [(c, hex(ord(c))) for c in title if ord(c) > 127]
        print("Non-ASCII characters found:", non_ascii)

    html_snippet_start = page.content().find('id="productTitle"')
    html = page.content()
    print()
    print("Raw HTML around productTitle (via page.content()):")
    print(repr(html[max(0, html_snippet_start - 20) : html_snippet_start + 200]))

    browser.close()
