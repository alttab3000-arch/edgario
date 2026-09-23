#!/usr/bin/env python3
"""Bundle the local UI refresh into the original single-file app at image build.

Only index.html is modified. Authentication, server.py and encrypted user data
are not touched. The original script/style and CSP contract remain single-file.
Run: python ui/build.py [path/to/index.html]
"""
from pathlib import Path
import re
import sys

MARKER = '<!-- EDGARIO_UI_REFRESH_V1 -->'
HERE = Path(__file__).resolve().parent


def build(path: Path) -> None:
    html = path.read_text(encoding='utf-8')
    if MARKER in html:
        print('Edgario UI refresh already bundled.')
        return
    for anchor in ('<style>', '</style>', '<script>', '</script>',
                   'const SERVER_MODE = false;', 'const REGISTRATION_OPEN = true;',
                   "\nrenderAuth('login');\n"):
        if html.count(anchor) != 1:
            raise RuntimeError(f'Unexpected source layout: {anchor!r}. No file was changed.')

    replacements = {
        '<meta name="theme-color" content="#87445b">': '<meta name="theme-color" content="#101214">',
        'Эдгарио — личное пространство для знакомств, важных дат и маленьких деталей.': 'Эдгарио — знакомства, встречи и личные заметки. Всё по делу, всё под рукой.',
        'Эдгарио — твой личный круг': 'Эдгарио — твоя движуха',
        'Открываем личное пространство…': 'Открываем твой штаб…',
        'Люди. Моменты. Маленькие детали.': 'ЛИЧНЫЙ ШТАБ',
        'Помнить важное.<br>Быть <em>ближе.</em>': 'Твой круг.<br>Твоя <em>движуха.</em>',
        'У каждого знакомства — своя история.<br>Сохрани то, что делает её особенной.': 'Без лишних глаз. Без лишней лирики.<br>Знакомства, встречи и всё, что зацепило.',
        "${icon('gift',14)}День рождения, который ты не забудешь": "${icon('flame',14)}Планы на вечер — без суеты",
        'Твоё личное пространство': 'ЛИЧНЫЙ ШТАБ',
        'Твой личный круг.': 'Твоя движуха — здесь.',
        'Знакомства, фотографии и важные даты.<br>Всё, что хочется держать под рукой.': 'Знакомства, встречи и личные заметки.<br>Всё по делу, всё под рукой.',
        'Войти в личный кабинет': 'Залететь в кабинет',
        'Создать личный кабинет': 'Создать свой кабинет',
        'сначала познакомимся?': 'сначала заглянешь?',
        'Посмотреть демо без регистрации': 'Глянуть, как всё устроено',
        'ЛИЧНЫЙ КРУГ': 'ЛИЧНЫЙ ШТАБ',
        'Мой круг': 'Моя движуха',
        'Открыть мой круг': 'Открыть мою движуху',
        'Избранное': 'Сочные',
        'Особенные люди': 'Сочные',
        'Убрать из избранного': 'Убрать из сочных',
        'В избранное': 'В сочные',
        'Добавить в избранное': 'Отметить как сочную',
        'Знакомства, к которым хочется возвращаться.': 'Те, кто особенно зацепил.',
        'Сохранённые истории. Всегда можно вернуть знакомство.': 'Пока без движухи. Вернуть можно в любой момент.',
        'Знакомства, важные даты и маленькие детали.': 'Кто зацепил, с кем на связи и что дальше по плану.',
        'Не просто имена в телефоне': 'ТВОЙ ЛИЧНЫЙ ШТАБ',
        'Хорошие отношения<br>начинаются с внимания.': 'Держи всё<br>под контролем.',
        'Запоминай любимые цветы, находи общие интересы<br>и не пропускай важные даты.': 'Знакомства, планы и детали —<br>чтобы не выпадать из движухи.',
        'Ближайшее событие': 'Ближайший движ',
        'Маленькие детали имеют значение.': 'Меньше лирики. Больше движухи.',
        'Маленькие детали': 'Что зацепило',
        'Любимые цветы, общие планы и то, что хочется запомнить.': 'Что зацепило, о чём договорились, что не забыть.',
        'Всё начинается с первого знакомства': 'Пока тихо. Пора добавить движ.',
        'Здесь будут особенные знакомства': 'Здесь будут самые сочные',
        'Нажми на сердечко в карточке, чтобы добавить её сюда.': 'Нажми на звезду в карточке, чтобы добавить её в сочные.',
        'Все большие истории начинаются с маленьких деталей.': 'Имя, фото, планы. Остальное — по ходу движухи.',
        'То, что хочется запомнить…': 'Что зацепило, о чём договорились, что не забыть…',
        'История только начинается': 'Движуха только начинается',
        'Georgia,serif': 'Arial,sans-serif',
        'letter-spacing="-10"': 'letter-spacing="-5"',
    }
    for old, new in replacements.items():
        if old not in html:
            raise RuntimeError(f'Missing UI anchor: {old!r}. No file was changed.')
        html = html.replace(old, new)

    mark = 'const mark=(size=37)=>`<svg class="brand-mark" width="${size}" height="${size}" viewBox="0 0 40 40" fill="none" aria-hidden="true"><path d="M7 5h28l-4 8H16l-2 5h15l-4 8H12l-2 5h16l-4 8H0Z" transform="translate(3 0) scale(.9)" fill="currentColor"/></svg>`;'
    html, count = re.subn(r'^const mark=.*$', lambda _: mark, html, flags=re.M)
    if count != 1:
        raise RuntimeError('Expected exactly one brand mark declaration.')
    palettes = "const AVATAR_PALETTES=[['#29343e','#3a4b58','#cdddeb'],['#2b3831','#405649','#c2e3ce'],['#342f3c','#4d425b','#e1d4ed'],['#3b3029','#594336','#f4d1b7'],['#2b3544','#3b4b63','#c9dcfa'],['#39312c','#55443a','#f5d2b9']];"
    html, count = re.subn(r'^const AVATAR_PALETTES=.*$', lambda _: palettes, html, flags=re.M)
    if count != 1:
        raise RuntimeError('Expected exactly one avatar palette declaration.')

    css = (HERE / 'theme.css').read_text(encoding='utf-8')
    javascript = (HERE / 'refresh.js').read_text(encoding='utf-8')
    if '<script' in javascript.lower() or '</script' in javascript.lower() or '</style' in css.lower():
        raise RuntimeError('Unsafe inline bundle delimiter.')
    html = html.replace('</style>', '\n/* EDGARIO_UI_REFRESH_V1 */\n' + css + '\n</style>', 1)
    html = html.replace("\nrenderAuth('login');\n", '\n' + javascript + "\nrenderAuth('login');\n", 1)
    html = html.replace('</head>', MARKER + '\n</head>', 1)
    assert html.count('<script>') == html.count('</script>') == 1
    assert html.count('<style>') == html.count('</style>') == 1
    assert html.count('const SERVER_MODE = false;') == 1
    assert html.count('const REGISTRATION_OPEN = true;') == 1
    assert html.index('Object.assign(STATUSES') < html.rindex("renderAuth('login');")
    path.write_text(html, encoding='utf-8')
    print('Bundled graphite UI and optional personal dates; original auth/storage preserved.')


if __name__ == '__main__':
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / 'index.html')
