"""Main colours of the application (Settings → Appearance).

Three colours chosen by the administrator - the header bar, the main colour (buttons, links,
"mine" bookings) and the accent - give all the CSS variables; darker and lighter shades are
calculated. A colour on which white text would be hard to read is rejected.
"""
import re

import app_settings

HEX_RE = re.compile(r'^#[0-9a-fA-F]{6}$')

PRESETS = {
    'blue': {'name': 'Blue (default)', 'header': '#0b2f5b', 'main': '#1d5fbf', 'accent': '#0d9488'},
    'green': {'name': 'Green', 'header': '#14532d', 'main': '#15803d', 'accent': '#0891b2'},
    'purple': {'name': 'Purple', 'header': '#3b0764', 'main': '#7c3aed', 'accent': '#db2777'},
    'graphite': {'name': 'Graphite', 'header': '#1f2937', 'main': '#334155', 'accent': '#0ea5e9'},
    'red': {'name': 'Red', 'header': '#450a0a', 'main': '#b91c1c', 'accent': '#0d9488'},
    'navy_gold': {'name': 'Navy and gold', 'header': '#0f172a', 'main': '#1e40af', 'accent': '#b45309'},
}
DEFAULT = PRESETS['blue']


def _rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _hex(rgb):
    return '#' + ''.join(f'{max(0, min(255, round(c))):02x}' for c in rgb)


def mix(color, other, t):
    """A colour `t` of the way towards `other` (0 = unchanged, 1 = other)."""
    a, b = _rgb(color), _rgb(other)
    return _hex([x + (y - x) * t for x, y in zip(a, b)])


def luminance(color):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_with_white(color):
    return 1.05 / (luminance(color) + 0.05)


def colors():
    """The current three colours (validated, defaults when missing)."""
    cfg = app_settings.get('theme')
    return {k: cfg.get(k) if HEX_RE.match(cfg.get(k) or '') else DEFAULT[k] for k in ('header', 'main', 'accent')}


def problem(header, main, accent):
    """Message (translated) when a colour is invalid or too light for white text, otherwise None."""
    from i18n import _
    for value in (header, main, accent):
        if not HEX_RE.match(value or ''):
            return _('Give the colours as #RRGGBB.')
    for value in (header, main, accent):
        if contrast_with_white(value) < 3:
            return _('The colour {color} is too light - white text on it would be hard to read.', color=value)
    return None


def css_variables(c=None):
    """CSS variables for :root."""
    c = c or colors()
    return {
        '--navy': c['header'], '--navy-2': mix(c['header'], '#ffffff', 0.12),
        '--brand': c['main'], '--brand-dark': mix(c['main'], '#000000', 0.2), '--accent': c['main'],
        '--mine': c['main'],
        '--accent2': c['accent'], '--accent2-dark': mix(c['accent'], '#000000', 0.2),
        '--accent2-bg': mix(c['accent'], '#ffffff', 0.88),
    }


def css(c=None):
    return ':root{' + ';'.join(f'{k}:{v}' for k, v in css_variables(c).items()) + '}'
