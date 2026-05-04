#!/usr/bin/env python3
"""
Convert TESTER_GUIDE.md to a standalone styled HTML file.
Usage: python3 generate_guide_html.py <output.html> [version]
"""

import re
import sys
from pathlib import Path

output_path = sys.argv[1] if len(sys.argv) > 1 else "guide.html"
version     = sys.argv[2] if len(sys.argv) > 2 else ""

src = Path("TESTER_GUIDE.md").read_text()


def md2html(text):
    # Fenced code blocks — handle before anything else
    def replace_code_block(m):
        code = m.group(1).replace("&", "&amp;").replace("<", "&lt;")
        return f"<pre><code>{code}</code></pre>"
    text = re.sub(r"```[^\n]*\n(.*?)```", replace_code_block, text, flags=re.DOTALL)

    lines = text.splitlines()
    out      = []
    in_table = False
    in_list  = False
    th_done  = False   # whether we've emitted at least one <th> row in current table

    for line in lines:
        # Horizontal rule
        if re.match(r"^-{3,}$", line.strip()):
            if in_list:  out.append("</ul>");   in_list  = False
            if in_table: out.append("</table>"); in_table = False; th_done = False
            out.append("<hr>")
            continue

        # Headings
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            if in_list:  out.append("</ul>");   in_list  = False
            if in_table: out.append("</table>"); in_table = False; th_done = False
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{m.group(2)}</h{lvl}>")
            continue

        # Blockquote
        if line.startswith(">"):
            if in_list:  out.append("</ul>");   in_list  = False
            if in_table: out.append("</table>"); in_table = False; th_done = False
            out.append(f"<blockquote>{line[1:].strip()}</blockquote>")
            continue

        # Table row
        if "|" in line and line.strip().startswith("|"):
            if not in_table:
                out.append("<table>")
                in_table = True
                th_done  = False
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            # Separator row (e.g. |---|---|) — next rows are body rows
            if all(re.match(r"^[-: ]+$", c) for c in cells):
                th_done = True
                continue
            tag = "th" if not th_done else "td"
            row = "".join(f"<{tag}>{c}</{tag}>" for c in cells)
            out.append(f"<tr>{row}</tr>")
            if not th_done:
                th_done = True   # first non-separator row treated as header
            continue

        # Close table if we were in one
        if in_table:
            out.append("</table>")
            in_table = False
            th_done  = False

        # Unordered list item
        if re.match(r"^- ", line):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{line[2:]}</li>")
            continue

        # Close list if we were in one
        if in_list:
            out.append("</ul>")
            in_list = False

        # Blank line
        if not line.strip():
            out.append("")
            continue

        # Normal paragraph line
        out.append(f"<p>{line}</p>")

    if in_list:  out.append("</ul>")
    if in_table: out.append("</table>")

    result = "\n".join(out)

    # Inline formatting
    result = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", result)
    result = re.sub(r"`([^`]+)`",      r"<code>\1</code>",     result)
    result = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', result)

    return result


body = md2html(src)

title = f"TalkTalk Tester Guide{' v' + version if version else ''}"

css = """
  body        { font-family: -apple-system, Helvetica Neue, sans-serif;
                max-width: 800px; margin: 48px auto; padding: 0 36px;
                color: #1a1a1a; line-height: 1.7; font-size: 15px; }
  h1          { font-size: 2em; border-bottom: 2px solid #e0e0e0;
                padding-bottom: 0.35em; margin-bottom: 0.5em; }
  h2          { font-size: 1.45em; border-bottom: 1px solid #ebebeb;
                padding-bottom: 0.2em; margin-top: 2.2em; }
  h3          { font-size: 1.1em; color: #222; margin-top: 1.6em; }
  code        { background: #f5f5f5; border-radius: 4px;
                padding: 2px 6px; font-size: 0.88em; }
  pre         { background: #f5f5f5; border-radius: 8px;
                padding: 16px; overflow-x: auto; margin: 1.2em 0; }
  pre code    { background: none; padding: 0; font-size: 0.9em; }
  table       { border-collapse: collapse; width: 100%; margin: 1.2em 0; }
  th, td      { border: 1px solid #ddd; padding: 9px 14px; text-align: left; }
  th          { background: #f0f0f0; font-weight: 600; }
  blockquote  { border-left: 4px solid #ccc; margin: 1em 0;
                padding: 8px 18px; color: #555; background: #fafafa;
                border-radius: 0 6px 6px 0; }
  ul          { padding-left: 1.6em; }
  li          { margin: 0.35em 0; }
  hr          { border: none; border-top: 1px solid #e2e2e2; margin: 2.5em 0; }
  a           { color: #0070c9; text-decoration: none; }
  a:hover     { text-decoration: underline; }
  p           { margin: 0.6em 0; }
  @media print {
    body { margin: 0; padding: 24px 36px; font-size: 13px; }
    a    { color: inherit; }
  }
"""

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>{css}</style>
</head>
<body>
{body}
</body>
</html>"""

Path(output_path).write_text(html)
print(f"    HTML written → {output_path}")
