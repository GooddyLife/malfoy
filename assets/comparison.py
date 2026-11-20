#!/usr/bin/env python3
"""Generates assets/comparison.svg, the animated version of the README comparison table.

    python assets/comparison.py        (from the repo root; stdlib only)

Edit ROWS, run it, commit both files. Every row must stay true to docs/comparison.md.
The SVG animates with CSS only: no script, no external font, so GitHub renders it in an <img>.
"""
from html import escape
from pathlib import Path

ROWS = [  # label, the official skills on their own, the same skills conducted by Malfoy
    ("Where you start", "Nine skills. You pick which, and when", "One entry. Eight phases, in order"),
    ("Persona", "A skill the build skill never mentions", "Phase 3. Not skipped"),
    ("Phase is done", "When the model judges it so", "When a script exits 0"),
    ("The spec", "Purpose, behaviour, subagents, actions", "+ metric, scope, handoff, data, envs"),
    ("Tests vs. spec", "\"Coverage maps against it\", in prose", "coverage names the untested use case"),
    ("Messy real records", "Not in the template", "spec S8 + coverage C6 on sandbox rows"),
    ("Live handoff", "Instructions only", "handoff: escalate or support-channels-only"),
    ("The agent user", "A setup guide, with queries to run", "perms + optional object/field rows"),
    ("Compiler gaps", "Reference pages to read", "lint: seven rules, file and line"),
    ("\"It's active\"", "A query in the docs", "active: pass or fail"),
    ("Edited after a pass", "Nothing records it", "STALE"),
    ("Spec per version", "Saved, not tied to what shipped", "active logs version, hashes, commit"),
    ("Your repo's rules", "Not looked for", "Read first. They override his"),
    ("Platform season", "Shipped agents stay unretested", "testrun + RETEST in status"),
    ("Success Metric live", "Written, never checked", "observe: warn if below target"),
]
GATES = ("coverage", "handoff", "perms", "lint", "active", "STALE", "S8")
CLOSING = "The official skills do the building. Malfoy makes them prove it."

LOOP, FIRST, STEP, ANSWER = 29.0, 1.2, 2.0, 0.8   # seconds
W, TOP, ROW = 880, 112, 34
X_LABEL, X_LEFT, X_MARK, X_RIGHT = 28, 196, 556, 580
H = TOP + ROW * len(ROWS) + 58


def pct(seconds: float) -> str:
    return f"{seconds / LOOP * 100:.2f}%"


def appear(name: str, at: float) -> str:
    return (f"@keyframes {name}{{0%,{pct(at)}{{opacity:0}}{pct(at + 0.4)},96%{{opacity:1}}"
            f"100%{{opacity:0}}}}")


css, body = [], []
for i, (label, official, malfoy) in enumerate(ROWS):
    start, answer, y = FIRST + i * STEP, FIRST + i * STEP + ANSWER, TOP + i * ROW
    css += [appear(f"a{i}", start), appear(f"b{i}", answer),
            # the official answer dims once Malfoy's arrives
            f"@keyframes d{i}{{0%,{pct(answer)}{{fill:#e6edf3}}{pct(answer + 0.5)},100%{{fill:#7d8590}}}}",
            f".a{i}{{animation:a{i} {LOOP:g}s linear infinite}}",
            f".o{i}{{animation:a{i} {LOOP:g}s linear infinite,d{i} {LOOP:g}s linear infinite}}",
            f".b{i}{{animation:b{i} {LOOP:g}s linear infinite}}"]
    word = malfoy.split(":")[0].split(" ")[0]
    right = (f'<tspan class="gate">{word}</tspan>{escape(malfoy[len(word):])}'
             if word in GATES else escape(malfoy))
    if i % 2 == 0:
        body.append(f'<rect x="1" y="{y - 22}" width="{W - 2}" height="{ROW}" fill="#11161d"/>')
    body += [
        f'<text class="anim label a{i}" x="{X_LABEL}" y="{y}">{escape(label)}</text>',
        f'<text class="anim cell o{i}" x="{X_LEFT}" y="{y}">{escape(official)}</text>',
        f'<path class="anim b{i}" d="M{X_MARK} {y - 5} l4 4 l8 -9" fill="none" stroke="#3fb950" '
        'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>',
        f'<text class="anim cell win b{i}" x="{X_RIGHT}" y="{y}">{right}</text>',
    ]
end = FIRST + len(ROWS) * STEP + 0.6
css += [appear("z", end), f".z{{animation:z {LOOP:g}s linear infinite}}"]
foot = TOP + ROW * len(ROWS)

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="Thirteen differences between the official Agentforce skills on their own and the same skills conducted by Malfoy. The full text is in the table below the image.">
<style>
text{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}}
.anim{{opacity:0}}
.title{{font-size:17px;font-weight:700;fill:#e6edf3}}
.head{{font-size:11px;font-weight:700;letter-spacing:.09em}}
.label{{font-size:12px;fill:#8b949e}}
.cell{{font-size:14px;fill:#e6edf3}}
.win{{fill:#e6edf3}}
.gate{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;fill:#3fb950;font-weight:700}}
.closing{{font-size:15px;font-weight:700;fill:#e6edf3}}
{"".join(css)}
@media (prefers-reduced-motion:reduce){{.anim{{animation:none!important;opacity:1}}}}
</style>
<rect width="{W}" height="{H}" rx="10" fill="#0d1117"/>
<text class="title" x="{X_LABEL}" y="38">The official skills, with and without him</text>
<text class="head" x="{X_LEFT}" y="74" fill="#8b949e">ON THEIR OWN</text>
<text class="head" x="{X_MARK}" y="74" fill="#3fb950">CONDUCTED BY MALFOY</text>
<line x1="0" y1="{TOP - 23}" x2="{W}" y2="{TOP - 23}" stroke="#30363d"/>
{chr(10).join(body)}
<line x1="0" y1="{foot - 4}" x2="{W}" y2="{foot - 4}" stroke="#30363d"/>
<text class="anim closing z" x="{W / 2:g}" y="{foot + 30}" text-anchor="middle">{escape(CLOSING)}</text>
<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="10" fill="none" stroke="#30363d"/>
</svg>
'''
out = Path(__file__).with_name("comparison.svg")
out.write_text(svg, encoding="utf-8", newline="\n")
print(f"{out.name}: {len(svg)} bytes, {W}x{H}, loop {LOOP:g}s")
