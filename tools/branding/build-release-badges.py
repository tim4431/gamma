"""Generate release download badges from the existing assistant brand marks."""
import xml.etree.ElementTree as ET

from branding import ROOT, FONT, write_svg


BRANDS = ROOT / 'frontend/src/shared/illustrations/brands'
BADGES = (
    ('codex', 'openai', 'Codex', 'Plugin ZIP', 104, 204, '#087f63', '#edf8f3'),
    ('claude-code', 'claude', 'Claude Code', 'Plugin ZIP', 148, 248, '#a34f32', '#fff3ec'),
    ('deepseek-harness', 'deepseek', 'DeepSeek Harness', 'dsh bundle', 204, 308, '#4059d8', '#eff2ff'),
)


for slug, icon, name, download, divider, width, color, background in BADGES:
    mark = ET.parse(BRANDS / f'{icon}.svg').getroot()
    paths = ''.join(
        f'<path d="{element.attrib["d"]}"/>'
        for element in mark.findall('{http://www.w3.org/2000/svg}path')
    )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="36" viewBox="0 0 {width} 36" role="img" aria-labelledby="title">
  <title id="title">{name}: download {download}</title>
  <rect x="0.5" y="0.5" width="{width - 1}" height="35" rx="8" fill="{background}" stroke="{color}" stroke-opacity="0.25"/>
  <g transform="translate(12 8) scale(0.833333)" fill="{color}">{paths}</g>
  <g font-family="{FONT}" fill="{color}">
    <text x="41" y="23" font-size="14" font-weight="600">{name}</text>
    <text x="{divider + 12}" y="23" font-size="13">{download}</text>
  </g>
  <path d="M{divider} 9 V27" stroke="{color}" stroke-opacity="0.2"/>
  <path d="M{width - 18} 12 V21 m-4 -4 4 4 4 -4 M{width - 23} 23 v2 h10 v-2" fill="none" stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
</svg>'''
    write_svg(f'gamma-release-{slug}', svg)
