"""Translation helper: python tools/i18n_check.py [--update]

Collects every text passed to _() in Python and templates and to t() in JavaScript, and compares
it with locales/*.json:
  * without arguments - lists missing and unused texts of every language (exit code 1 when missing),
  * --update          - rewrites locales/en.json with all texts (the template for translators)
                        and adds missing texts to the other languages with an empty value.
An empty value means "not translated" - the English text is shown.
"""
import ast
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALES = os.path.join(ROOT, 'locales')

# _('...') / _("...") in Python and Jinja, t('...') in JavaScript (only literal texts).
PATTERNS = [re.compile(r"""(?<![\w.])_\(\s*'((?:[^'\\]|\\.)*)'"""), re.compile(r'''(?<![\w.])_\(\s*"((?:[^"\\]|\\.)*)"''')]
JS_PATTERNS = [re.compile(r"""(?<![\w.])t\(\s*'((?:[^'\\]|\\.)*)'"""), re.compile(r'''(?<![\w.])t\(\s*"((?:[^"\\]|\\.)*)"''')]

# Texts translated through a variable (dictionaries and lists in the code).
DYNAMIC = [
    # weekdays (i18n.WEEKDAYS, plan.js)
    'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun',
    'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday',
    # booking.TYPE_NAMES, editor.js NAMES
    'Desk', 'Room', 'Booth', 'Wall', 'Zone', 'Label',
    # permissions.ROLES
    'Administrator', 'Floor plan and team editor', 'User',
    # sso.PROVIDERS
    'Microsoft (Entra ID / Azure AD)', 'Google', 'Other OpenID Connect provider',
    # common.js STATUS_LABELS
    'Free', 'Your booking', 'Taken', 'Taken by someone from your team', 'Unavailable', 'Permanent desk',
    'Your permanent desk', 'Part of the day taken',
    # theme.PRESETS
    'Blue (default)', 'Green', 'Purple', 'Graphite', 'Red', 'Navy and gold',
    # booking._collision (text chosen by a condition)
    'You already have a booking: {place} ({hours}).', 'This person already has a booking: {place} ({hours}).',
]


def _unescape(s):
    return s.replace("\\'", "'").replace('\\"', '"').replace('\\n', '\n')


def _python_texts(src):
    """_('...') calls in Python - through the syntax tree, so 'a' 'b' on two lines is one text, as at runtime."""
    out = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == '_' and node.args \
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            out.add(node.args[0].value)
    return out


def collect():
    texts = set(DYNAMIC)
    for path in glob.glob(os.path.join(ROOT, '*.py')) + glob.glob(os.path.join(ROOT, 'routes', '*.py')):
        texts |= _python_texts(open(path, encoding='utf-8').read())
    for path in glob.glob(os.path.join(ROOT, 'templates', '*.html')):
        src = open(path, encoding='utf-8').read()
        for p in PATTERNS + JS_PATTERNS:           # Jinja and scripts inside templates
            texts.update(_unescape(m) for m in p.findall(src))
    for path in glob.glob(os.path.join(ROOT, 'static', 'js', '*.js')):
        src = open(path, encoding='utf-8').read()
        for p in JS_PATTERNS:
            texts.update(_unescape(m) for m in p.findall(src))
    return texts


def main():
    update = '--update' in sys.argv
    texts = collect()
    missing_total = 0
    for path in sorted(glob.glob(os.path.join(LOCALES, '*.json'))):
        code = os.path.splitext(os.path.basename(path))[0]
        data = json.load(open(path, encoding='utf-8'))
        name = data.get('_language', code)
        current = {k: v for k, v in data.items() if not k.startswith('_')}
        if code == 'en':
            if update:
                out = {'_language': name, **{k: k for k in sorted(texts, key=str.lower)}}
                json.dump(out, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
                print(f'en.json: {len(texts)} texts')
            continue
        missing = sorted(t for t in texts if not current.get(t))
        unused = sorted(set(current) - texts)
        missing_total += len(missing)
        print(f'{code}.json ({name}): {len(texts) - len(missing)}/{len(texts)} translated, '
              f'{len(missing)} missing, {len(unused)} unused')
        for m in missing[:200]:
            print('   missing:', m)
        for u in unused[:50]:
            print('   unused: ', u)
        if update:
            out = {'_language': name}
            for k in sorted(texts, key=str.lower):
                out[k] = current.get(k, '')
            json.dump(out, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    sys.exit(1 if missing_total and not update else 0)


if __name__ == '__main__':
    main()
