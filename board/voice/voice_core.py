"""Small, strict command vocabulary used after offline speech recognition."""
import json
import re


COMMANDS = {
    '开灯': True,
    '打开灯': True,
    '关灯': False,
    '关闭灯': False,
}


def recognized_text(result):
    data = json.loads(result)
    if not isinstance(data, dict):
        raise ValueError('Invalid recognition result')
    text = data.get('text', '')
    if not isinstance(text, str):
        raise ValueError('Invalid recognition text')
    return text.strip()


def choose_command(texts):
    recognized = []
    for text in texts:
        normalized = re.sub(r'[\s，。,.!！?？]+', '', text)
        if normalized in COMMANDS:
            recognized.append(COMMANDS[normalized])
    if not recognized:
        return None
    if len(set(recognized)) > 1:
        raise ValueError('Heard conflicting light commands')
    return recognized[0]
