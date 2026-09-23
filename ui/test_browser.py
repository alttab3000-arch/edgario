#!/usr/bin/env python3
"""Exercise the real app in an isolated local server with synthetic data only."""
from pathlib import Path
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'ui-results'
OUT.mkdir(exist_ok=True)
checks = []

def passed(name):
    checks.append(name)
    print('PASS:', name, flush=True)

def no_overflow(page, label):
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), label
    passed('No horizontal overflow: ' + label)

def run():
    with tempfile.TemporaryDirectory(prefix='edgario-ui-test-') as directory:
        temp = Path(directory)
        # Deliberately do not copy bootstrap_accounts.json or any user data.
        for name in ('index.html', 'server.py'):
            shutil.copyfile(ROOT / name, temp / name)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        env = {**os.environ, 'EDGARIO_ORIGIN': origin, 'EDGARIO_DATA_DIR': str(temp / 'data'), 'EDGARIO_ALLOW_REGISTRATION': '1'}
        server = subprocess.Popen([sys.executable, str(temp / 'server.py'), '--port', str(port)], env=env, stdout=subprocess.DEVNULL)
        try:
            for _ in range(100):
                try:
                    with urllib.request.urlopen(origin + '/healthz', timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError('Isolated test server did not start')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                context = browser.new_context(viewport={'width': 1440, 'height': 960}, accept_downloads=True)
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin)
                expect(page.get_by_role('button', name='Залететь в кабинет', exact=True)).to_be_visible()
                assert page.evaluate("getComputedStyle(document.body).backgroundColor") == 'rgb(16, 18, 20)'
                page.screenshot(path=str(OUT / '01-login-desktop.png'), full_page=True)
                passed('Real server boots with dark theme, new copy and working CSP')
                page.locator('[data-act="demo"]').click()
                expect(page.locator('.page-heading h1')).to_have_text('Моя движуха')
                expect(page.locator('.stat')).to_have_count(4)
                expect(page.locator('.person-card')).to_have_count(6)
                expect(page.locator('.person-card').first.locator('.personal-date-row')).to_have_count(2)
                assert page.evaluate("STATUSES.dating.name") == 'Встречаемся'
                assert page.evaluate("STATUSES.new.name") == 'Знакомство'
                passed('Legacy contacts render and old relationship statuses retain their meanings')
                page.screenshot(path=str(OUT / '02-dashboard-desktop.png'), full_page=True)
                page.locator('.heading-row [data-act="add"]').click()
                form = page.locator('#personForm')
                form.locator('[name="name"]').fill('Тестовая карточка')
                form.locator('[name="city"]').fill('Казань')
                form.locator('[name="status"]').select_option('planned')
                form.locator('[name="pmsStart"]').fill('2026-09-21')
                form.locator('[name="pmsEnd"]').fill('2026-09-24')
                form.locator('[name="ovulationDate"]').fill('2026-10-06')
                form.locator('#savePerson').click()
                expect(page.locator('#mainDialog')).not_to_be_visible()
                card = page.locator('.person-card').filter(has_text='Тестовая карточка')
                expect(card.locator('.personal-dates')).to_contain_text('21.09.2026 — 24.09.2026')
                expect(card.locator('.personal-dates')).to_contain_text('06.10.2026')
                expect(page.locator('.stat').nth(2)).to_contain_text('1')
                card.click()
                expect(page.locator('.personal-dates-panel')).to_contain_text('06.10.2026')
                passed('Form saves separate PMS range and ovulation date; card and details agree')
                page.locator('#mainDialog [data-act="edit"]').click()
                form = page.locator('#personForm')
                form.locator('[name="pmsEnd"]').fill('2026-09-20')
                form.locator('#savePerson').click()
                expect(form.locator('#personError')).to_contain_text('раньше начала')
                form.locator('[name="pmsEnd"]').fill('2026-09-24')
                page.set_viewport_size({'width': 390, 'height': 844})
                form.locator('.personal-dates-editor').scroll_into_view_if_needed()
                page.screenshot(path=str(OUT / '03-personal-dates-mobile.png'), full_page=True)
                assert page.locator('#mainDialog').evaluate('(d) => d.scrollWidth <= d.clientWidth + 1')
                form.locator('[data-clear-personal-dates]').click()
                for field in ('pmsStart', 'pmsEnd', 'ovulationDate'):
                    expect(form.locator('[name="' + field + '"]')).to_have_value('')
                form.locator('#savePerson').click()
                expect(page.locator('#mainDialog')).not_to_be_visible()
                passed('Inverted ranges rejected; clear button removes all three dates; editor fits mobile')
                for width in (360, 390, 768, 1440):
                    page.set_viewport_size({'width': width, 'height': 900})
                    no_overflow(page, f'dashboard {width}px')
                page.set_viewport_size({'width': 390, 'height': 844})
                page.screenshot(path=str(OUT / '04-dashboard-mobile.png'), full_page=True)
                page.locator('[data-act="filter"][data-filter="planned"]').click()
                expect(page.locator('.person-card')).to_have_count(1)
                page.locator('[data-act="filter"][data-filter="all"]').click()
                page.locator('[data-act="view"][data-view="list"]').click()
                no_overflow(page, 'mobile list view')
                page.locator('[data-act="view"][data-view="grid"]').click()
                passed('Status filtering and list/grid switching work')
                page.set_viewport_size({'width': 1440, 'height': 960})
                for route in ('favorites', 'calendar', 'notes', 'archive', 'settings'):
                    page.locator('.sidebar [data-act="nav"][data-page="' + route + '"]').first.click()
                    no_overflow(page, route)
                passed('All existing navigation screens render')
                page.locator('[data-act="logout"]').click()
                page.locator('[data-act="auth-tab"][data-tab="signup"]').click()
                auth = page.locator('#authForm')
                auth.locator('[name="name"]').fill('Изолированный тест')
                auth.locator('[name="email"]').fill('synthetic-ui-test')
                auth.locator('[name="password"]').fill('synthetic-ui-test-password')
                auth.locator('button[type="submit"]').click()
                expect(page.locator('.page-heading h1')).to_have_text('Моя движуха', timeout=15000)
                expect(page.locator('.person-card')).to_have_count(0)
                page.locator('.heading-row [data-act="add"]').click()
                form = page.locator('#personForm')
                form.locator('[name="name"]').fill('Только тестовые данные')
                form.locator('[name="status"]').select_option('done')
                form.locator('[name="pmsStart"]').fill('2026-09-21')
                form.locator('[name="pmsEnd"]').fill('2026-09-24')
                form.locator('[name="ovulationDate"]').fill('2026-10-06')
                form.locator('#savePerson').click()
                expect(page.locator('#mainDialog')).not_to_be_visible(timeout=15000)
                page.reload()
                auth = page.locator('#authForm')
                auth.locator('[name="email"]').fill('synthetic-ui-test')
                auth.locator('[name="password"]').fill('synthetic-ui-test-password')
                auth.locator('button[type="submit"]').click()
                expect(page.locator('.person-card .personal-dates')).to_contain_text('06.10.2026', timeout=15000)
                expect(page.locator('.person-card')).to_contain_text('Отжарил')
                passed('Real signup/login, AES-GCM server save and reload preserve manual dates and status')
                page.locator('.sidebar [data-page="settings"]').first.click()
                with page.expect_download() as download_info:
                    page.locator('[data-act="export"]').click()
                backup = Path(download_info.value.path()).read_text()
                assert 'Только тестовые данные' not in backup
                assert '2026-10-06' not in backup
                payload = json.loads(backup)
                assert payload['format'] == 'edgario-backup-v1'
                imported = page.evaluate('''async (backup) => {
                  const r = backup.record;
                  const key = await deriveKey('synthetic-ui-test-password', r.salt, r.iterations);
                  const restored = await unseal(r, key);
                  return normalizeImportedVault(restored);
                }''', payload)
                assert imported[0]['pmsStart'] == '2026-09-21'
                assert imported[0]['pmsEnd'] == '2026-09-24'
                assert imported[0]['ovulationDate'] == '2026-10-06'
                assert imported[0]['status'] == 'done'
                passed('Downloaded backup stays encrypted and round-trip import preserves all new fields')
                validation = page.evaluate('''() => {
                  const old = demoVault();
                  const normalized = normalizeImportedVault(old);
                  const outcomes = [normalized.every(p => p.pmsStart === '' && p.pmsEnd === '' && p.ovulationDate === '')];
                  for (const value of ['2026-02-30', '2026-02-29', '1899-01-01', '<img onerror=x>', 42]) {
                    const input = demoVault(); input.contacts[0].ovulationDate = value;
                    try { normalizeImportedVault(input); outcomes.push(false); } catch { outcomes.push(true); }
                  }
                  return outcomes;
                }''')
                assert all(validation)
                passed('Old backup compatibility and malformed/imported-date validation')
                assert errors == [], errors
                passed('No browser JavaScript errors during all checks')
                browser.close()
        finally:
            server.terminate()
            server.wait(timeout=10)
            (OUT / 'checks.json').write_text(json.dumps({'checks': checks, 'passed': len(checks)}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    run()
    print(f'{len(checks)} browser checks passed. No production accounts were accessed.')
