#!/usr/bin/env python3
"""Render the GMSE01 README card from a verified report or archived snapshot."""

import argparse
import json
from html import escape
from pathlib import Path


def args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--date", required=True, help="YYYY-MM-DD of the measurement")
    parser.add_argument("--revision", required=True, help="source revision measured")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--report", type=Path, help="build/GMSE01/report.json")
    source.add_argument("--snapshot", type=Path, help="archived measure JSON")
    return parser.parse_args()


def main():
    options = args()
    data = json.loads((options.report or options.snapshot).read_text())
    measures = data.get("measures", data)
    fuzzy = float(measures["fuzzy_match_percent"])
    exact = float(measures["matched_code_percent"])
    linked = float(measures["complete_code_percent"])
    exact_functions = int(measures["matched_functions"])
    total_functions = int(measures["total_functions"])
    linked_units = int(measures["complete_units"])
    total_units = int(measures["total_units"])
    assert 0 <= linked <= exact <= fuzzy <= 100

    # Three independent tracks: fuzzy is a similarity score, while exact and
    # linked are byte coverage. Combining them into a stacked bar misleads.
    tracks = [
        ("FUZZY SIMILARITY", fuzzy, "fuzzy", "#7BE8E4", 0),
        ("BYTE-PERFECT CODE", exact, "exact", "#FFD270", 1),
        ("SOURCE-LINKED CODE", linked, "linked", "#FFA693", 2),
    ]
    bars = []
    for label, value, kind, color, index in tracks:
        y = 247 + index * 49
        width = 520 * value / 100
        bars.append(f'''
    <text x="54" y="{y + 13}" class="bar-label">{label}</text>
    <rect x="339" y="{y}" width="520" height="16" rx="8" fill="#1D3443"/>
    <rect x="339" y="{y}" width="{width:.2f}" height="16" rx="8" fill="url(#{kind})"/>
    <path d="M{339 + width:.2f} {y - 4}v24" stroke="{color}" stroke-width="2" opacity=".85"/>
    <text x="946" y="{y + 14}" text-anchor="end" class="bar-value" fill="{color}">{value:.2f}%</text>''')

    date = escape(options.date)
    revision = escape(options.revision)
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 420" width="1000" height="420" role="img" aria-labelledby="title description">
  <title id="title">GMSE01 decompilation progress: {fuzzy:.2f}% fuzzy similarity, {exact:.2f}% byte-perfect code, {linked:.2f}% source-linked code</title>
  <desc id="description">Verified {date} at source revision {revision}. Fuzzy similarity is an approximate score, not exact byte coverage. {exact_functions:,} of {total_functions:,} functions match exactly; {linked_units} of {total_units} object files are linked from source.</desc>
  <defs>
    <linearGradient id="background" x1="0" y1="0" x2="1" y2="1">
      <stop stop-color="#101F2C"/><stop offset=".55" stop-color="#102B36"/><stop offset="1" stop-color="#17323A"/>
    </linearGradient>
    <linearGradient id="fuzzy" x1="0" x2="1">
      <stop stop-color="#30BFD6"/><stop offset="1" stop-color="#8AF4D5"/>
    </linearGradient>
    <linearGradient id="exact" x1="0" x2="1">
      <stop stop-color="#E99A56"/><stop offset="1" stop-color="#FFE589"/>
    </linearGradient>
    <linearGradient id="linked" x1="0" x2="1">
      <stop stop-color="#F07B84"/><stop offset="1" stop-color="#FFC18A"/>
    </linearGradient>
    <radialGradient id="sun">
      <stop stop-color="#FFE9A4" stop-opacity=".86"/>
      <stop offset=".3" stop-color="#FFBD73" stop-opacity=".28"/>
      <stop offset="1" stop-color="#FFB86E" stop-opacity="0"/>
    </radialGradient>
    <pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse">
      <path d="M32 0H0V32" fill="none" stroke="#B3EFF0" stroke-opacity=".055"/>
    </pattern>
    <pattern id="ticks" width="10.4" height="16" patternUnits="userSpaceOnUse">
      <path d="M9.9 0V16" stroke="#102635" stroke-width="1" stroke-opacity=".5"/>
    </pattern>
    <clipPath id="bar-clip"><rect x="339" y="0" width="520" height="420" rx="8"/></clipPath>
    <style>
      .mono {{ font-family: 'DejaVu Sans Mono', 'Courier New', monospace; font-weight: 700; letter-spacing: 1.7px; }}
      .sans {{ font-family: 'DejaVu Sans', Arial, sans-serif; }}
      .bar-label {{ fill: #D0E5E5; font: 700 13px 'DejaVu Sans Mono', 'Courier New', monospace; letter-spacing: .5px; }}
      .bar-value {{ font: 700 17px 'DejaVu Sans Mono', 'Courier New', monospace; }}
    </style>
  </defs>
  <rect x="1" y="1" width="998" height="418" rx="23" fill="url(#background)" stroke="#42717C" stroke-width="2"/>
  <rect x="2" y="2" width="996" height="416" rx="22" fill="url(#grid)"/>
  <path d="M24 204H976" stroke="#88C9C6" stroke-opacity=".25"/>
  <path d="M24 386H976" stroke="#88C9C6" stroke-opacity=".25"/>
  <rect x="47" y="34" width="12" height="12" rx="2" fill="#8AEAD8"/>
  <text x="72" y="45" fill="#95D4D3" font-size="14" class="mono">GMSE01 / RECONSTRUCTION SIGNAL</text>
  <text x="54" y="117" fill="#FAF6E8" font-size="50" font-weight="800" class="sans" letter-spacing="-2">Sunshine in source</text>
  <text x="56" y="153" fill="#B8D0D1" font-size="17" class="sans">North American Rev 0 · verified decompilation snapshot</text>
  <text x="55" y="188" fill="#78E5E2" font-size="15" class="mono">{fuzzy:.2f}% FUZZY SIMILARITY</text>
  <circle cx="872" cy="113" r="110" fill="url(#sun)"/>
  <circle cx="872" cy="113" r="61" fill="none" stroke="#FFCE87" stroke-opacity=".38" stroke-width="2"/>
  <circle cx="872" cy="113" r="43" fill="none" stroke="#FFE2A3" stroke-opacity=".7" stroke-width="2"/>
  <circle cx="872" cy="113" r="24" fill="#FFE3A1" opacity=".9"/>
  <path d="M872 28v18M872 180v18M787 113h18M939 113h18M811 52l13 13M920 161l13 13M933 52l-13 13M824 161l-13 13" stroke="#FFD08B" stroke-opacity=".64" stroke-width="3" stroke-linecap="round"/>
  <text x="54" y="229" fill="#84A7AD" font-size="12" class="mono">MEASURE</text>
  <text x="946" y="229" text-anchor="end" fill="#84A7AD" font-size="12" class="mono">PROGRESS</text>
{''.join(bars)}
  <g clip-path="url(#bar-clip)" fill="url(#ticks)">
    <rect x="339" y="247" width="520" height="16"/>
    <rect x="339" y="296" width="520" height="16"/>
    <rect x="339" y="345" width="520" height="16"/>
  </g>
  <text x="54" y="405" fill="#B3D4D3" font-size="12" class="sans">{exact_functions:,} / {total_functions:,} exact functions</text>
  <text x="389" y="405" fill="#B3D4D3" font-size="12" class="sans">{linked_units} / {total_units} source-linked objects</text>
  <text x="946" y="405" text-anchor="end" fill="#90B3B6" font-size="11" class="mono">{date} · {revision}</text>
</svg>
'''
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(svg)


if __name__ == "__main__":
    main()
