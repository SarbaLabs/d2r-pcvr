"""D2RLoader text and voice language preferences; no keyboard or gameplay changes."""
import json
from pathlib import Path
import re
import mod_identity

LANGUAGES = {'': 'Default (game language)', 'enUS': 'English', 'deDE': 'German',
             'esES': 'Spanish (Spain)', 'frFR': 'French', 'itIT': 'Italian',
             'koKR': 'Korean', 'plPL': 'Polish', 'ruRU': 'Russian',
             'zhCN': 'Chinese (Simplified)', 'zhTW': 'Chinese (Traditional)',
             'esMX': 'Spanish (Latin America)', 'jaJP': 'Japanese', 'ptBR': 'Portuguese (Brazil)'}


def replace_value(text, key, value, section=None):
    # Preserve comments, other settings and section placement; refuse ambiguity.
    pattern = re.compile(r'^(\s*' + re.escape(key) + r'\s*=\s*)[^\r\n]*', re.M)
    matches = list(pattern.finditer(text))
    if len(matches) > 1:
        raise ValueError('Duplicate configuration key: ' + key)
    if matches:
        return pattern.sub(lambda m: m[1] + value, text, count=1)
    if section is None:
        # TOML language keys belong at root, before any table.
        return key + ' = ' + value + '\n' + text
    header = re.search(r'^\[' + re.escape(section) + r'\]\s*$', text, re.M)
    if header:
        return text[:header.end()] + '\n' + key + '=' + value + text[header.end():]
    return text.rstrip() + '\n\n[' + section + ']\n' + key + '=' + value + '\n'


def preferences(game):
    result = {'text_locale': '', 'audio_locale': ''}
    source = Path(game) / 'd2rloader/config/d2rloader.toml'
    if source.is_file():
        # Only root TOML keys belong to D2RLoader; table keys are unrelated.
        text = source.read_text(encoding='utf-8-sig').split('[')[0]
        for key in result:
            matches = re.findall(r'^\s*' + key + r'\s*=\s*[\"\']([^\"\']*)[\"\']', text, re.M)
            if len(matches) == 1 and matches[0] in LANGUAGES:
                result[key] = matches[0]
    return result


def preference_updates(game, choices):
    path = Path(game) / 'd2rloader/config/d2rloader.toml'
    text = path.read_text(encoding='utf-8-sig') if path.exists() else ''
    # Restrict replacement to the TOML root. Preserve all other tables verbatim.
    match = re.search(r'^\s*\[', text, re.M)
    root, tail = (text[:match.start()], text[match.start():]) if match else (text, '')
    for key in ('text_locale', 'audio_locale'):
        if choices[key] not in LANGUAGES:
            raise ValueError('Unsupported language: ' + choices[key])
        root = replace_value(root, key, json.dumps(choices[key]))
    return {path: (root + tail).encode()}
