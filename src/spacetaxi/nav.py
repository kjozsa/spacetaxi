"""The bottom navigation bar of the server's HTML pages: one source for every page.

`with_nav(html, current)` adds the bar to a page `spacetaxi serve` sends, the link of `current`
marked with `aria-current="page"`. A page with a `<!--NAV-->` placeholder (the viewer, `/watch`)
gets only the links there, inside its own bar; any other gets a fixed bar at the bottom of the
window, `--sitenav-h` high, and a spacer of that height at the end of its content. Pages built
for `file://` (`spacetaxi view --out`, `spacetaxi play --out`) never get it: there is no server
to link to.
"""

import base64
import html as _html
from pathlib import Path

WEB_DIR = Path(__file__).parent / "web"

NAV_PAGES = (("/", "LEADERBOARD"), ("/watch", "WATCH"), ("/standings", "STANDINGS"),
             ("/mission", "MISSION"), ("/lessons", "LESSONS"), ("/fly", "FLY"),
             ("/card", "CARD"), ("/brief", "BRIEF"),
             ("/slides", "SLIDES"))

PLACEHOLDER = "<!--NAV-->"

NAV_CSS = """
:root { --sitenav-h: 40px; }
.sitenav-space { height: var(--sitenav-h); }
nav.sitenav {
  position: fixed; left: 0; right: 0; bottom: 0; z-index: 50; height: var(--sitenav-h);
  box-sizing: border-box; display: flex; align-items: center; gap: 4px; padding: 0 8px;
  overflow-x: auto; overflow-y: hidden; white-space: nowrap; scrollbar-width: thin;
  background: linear-gradient(#07050f, #120d2e); border-top: 2px solid #7a5cff;
  font: 11px "Press Start 2P", "Courier New", monospace; letter-spacing: 0;
}
nav.sitenav a {
  flex: none; color: #9a94c4; text-decoration: none; padding: 7px 9px; border: 2px solid transparent;
}
nav.sitenav a:hover { color: #2ef2ff; text-shadow: 0 0 8px #2ef2ff; }
nav.sitenav a[aria-current="page"] { color: #120f00; background: #ffd23f; border-color: #ffd23f; text-shadow: none; }
nav.sitenav a:focus-visible { outline: 2px solid #ffd23f; outline-offset: -2px; }
"""


def nav_links(current: str) -> str:
    """The links of the bar, `current` marked."""
    return "".join('<a href="{}"{}>{}</a>'.format(
        _html.escape(href), ' aria-current="page"' if href == current else "", _html.escape(label))
        for href, label in NAV_PAGES)


def with_nav(page: str, current: str) -> str:
    """`page` with the navigation bar, `current` (a path of `NAV_PAGES`) marked."""
    if PLACEHOLDER in page:
        return page.replace(PLACEHOLDER, '<nav class="barnav" aria-label="Pages">{}</nav>'.format(
            nav_links(current)))
    css = NAV_CSS
    if "@font-face" not in page:
        font = base64.b64encode((WEB_DIR / "PressStart2P-Regular.ttf").read_bytes()).decode("ascii")
        css = ('@font-face { font-family: "Press Start 2P"; '
               f'src: url(data:font/ttf;base64,{font}) format("truetype"); }}' + css)
    bar = (f'<style>{css}</style><div class="sitenav-space" aria-hidden="true"></div>'
           f'<nav class="sitenav" aria-label="Pages">{nav_links(current)}</nav>\n')
    at = page.rindex("</body>")
    return page[:at] + bar + page[at:]
