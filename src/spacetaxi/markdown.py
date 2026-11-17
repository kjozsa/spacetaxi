"""A small Markdown renderer for the server's `/brief` and `/card` pages.

It knows what `docs/brief.md` and `docs/participant-card.md` use: `#` headings, paragraphs,
`-` and `1.` lists (an item continues on lines indented under it), fenced code blocks, and
inline `` `code` ``, `**bold**` and `[links](url)`. Everything else is a paragraph of text.
All text is HTML-escaped.
"""

import html
import re

HEADING = re.compile(r"(#{1,6})\s+(.*)")
BULLET = re.compile(r"[-*]\s+(.*)")
NUMBERED = re.compile(r"\d+[.)]\s+(.*)")
INLINE = re.compile(r"`([^`]+)`|\*\*(.+?)\*\*|\[([^\]]+)\]\(([^)\s]+)\)")


def inline(text: str) -> str:
    """Escape `text` and render its inline code, bold and links."""
    out, pos = [], 0
    for m in INLINE.finditer(text):
        out.append(html.escape(text[pos:m.start()], quote=False))
        code, bold, label, url = m.groups()
        if code is not None:
            out.append(f"<code>{html.escape(code, quote=False)}</code>")
        elif bold is not None:
            out.append(f"<strong>{inline(bold)}</strong>")
        else:
            out.append(f'<a href="{html.escape(url)}">{inline(label)}</a>')
        pos = m.end()
    out.append(html.escape(text[pos:], quote=False))
    return "".join(out)


def render(text: str) -> str:
    """The HTML of the Markdown `text`."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
        elif stripped.startswith("```"):
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1  # the closing fence
            out.append(f"<pre><code>{html.escape(chr(10).join(body), quote=False)}</code></pre>")
        elif m := HEADING.fullmatch(stripped):
            level = len(m.group(1))
            out.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
            i += 1
        elif BULLET.fullmatch(stripped) or NUMBERED.fullmatch(stripped):
            pattern, tag = (BULLET, "ul") if BULLET.fullmatch(stripped) else (NUMBERED, "ol")
            items: list[list[str]] = []
            while i < len(lines):
                m = pattern.fullmatch(lines[i].strip())
                if m and not lines[i][:1].isspace():
                    items.append([m.group(1)])
                elif items and lines[i][:1].isspace() and lines[i].strip():
                    items[-1].append(lines[i].strip())  # continuation of the item
                else:
                    break
                i += 1
            lis = "".join(f"<li>{inline(' '.join(item))}</li>" for item in items)
            out.append(f"<{tag}>{lis}</{tag}>")
        else:
            para = []
            while i < len(lines) and lines[i].strip() and not starts_block(lines[i].strip()):
                para.append(lines[i].strip())
                i += 1
            out.append(f"<p>{inline(' '.join(para))}</p>")
    return "\n".join(out)


def starts_block(stripped: str) -> bool:
    return bool(stripped.startswith("```") or HEADING.fullmatch(stripped)
                or BULLET.fullmatch(stripped) or NUMBERED.fullmatch(stripped))
