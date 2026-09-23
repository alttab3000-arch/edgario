/* Edgario UI refresh. Loaded inside the existing CSP-hashed script before boot.
   No new storage, requests, analytics, medical predictions or inferred statuses. */
(() => {
  'use strict';
  Object.assign(STATUSES, {
    planned: {name: 'Ещё надо отжарить', color: 'sand'},
    done: {name: 'Отжарил', color: 'green'}
  });
  ICONS.flame = '<path d="M12 3c1 5 6 6 6 11a6 6 0 0 1-12 0c0-3 2-5 3-6 0 3 2 4 3 4 2-3 1-6 0-9Z"/>';
  ICONS.star = '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z"/>';

  const dateFields = ['pmsStart', 'pmsEnd', 'ovulationDate'];
  const dateLabels = {pmsStart: 'ПМС — начало', pmsEnd: 'ПМС — окончание', ovulationDate: 'Овуляция — дата'};
  function validPersonalDate(value) {
    return typeof value === 'string' && validISO(value) && value >= '1900-01-01' && value <= '2100-12-31';
  }
  function readPersonalDates(source) {
    const dates = {};
    for (const field of dateFields) {
      const value = source[field] ?? '';
      if (typeof value !== 'string' || (value !== '' && !validPersonalDate(value))) {
        throw new Error('Проверь поле «' + dateLabels[field] + '»: нужна дата с 1900 по 2100 год.');
      }
      dates[field] = value;
    }
    if (dates.pmsStart && dates.pmsEnd && dates.pmsEnd < dates.pmsStart) {
      throw new Error('Окончание ПМС не может быть раньше начала.');
    }
    return dates;
  }
  function showDate(value) {
    if (!validPersonalDate(value)) return '';
    return value.slice(8, 10) + '.' + value.slice(5, 7) + '.' + value.slice(0, 4);
  }
  function personalDatesHTML(person, compact = false) {
    const start = showDate(person.pmsStart), end = showDate(person.pmsEnd);
    const pms = start && end ? start + ' — ' + end : start ? 'с ' + start : end ? 'до ' + end : 'Не указано';
    const ovulation = showDate(person.ovulationDate) || 'Не указано';
    return `<div class="personal-dates ${compact ? 'compact' : ''}" aria-label="Личные даты"><div class="personal-date-row"><span>ПМС</span><span>${esc(pms)}</span></div><div class="personal-date-row"><span>Овуляция</span><span>${esc(ovulation)}</span></div></div>`;
  }

  // Original keys retain their meaning. New labels are manual choices only.
  const originalStatusPill = statusPill;
  statusPill = function(status) {
    if (status === 'planned' || status === 'done') {
      const entry = STATUSES[status];
      return `<span class="pill ${entry.color}">${icon('flame', 13)}${entry.name}</span>`;
    }
    return originalStatusPill(status);
  };
  const originalPeoplePage = peoplePage;
  peoplePage = function() {
    const html = originalPeoplePage();
    if (S.page !== 'people') return html;
    const active = S.vault.contacts.filter(person => !person.archived);
    const meetings = upcomingEvents().filter(event => event.type === 'meeting' && daysBetween(noon(), event.date) <= 30).length;
    const items = [
      [active.length, 'В движухе', 'people'],
      [active.filter(person => person.status === 'done').length, 'Отжарил', 'flame'],
      [active.filter(person => person.status === 'planned').length, 'Ещё надо отжарить', 'clock'],
      [meetings, 'Встречи за 30 дней', 'calendar']
    ];
    const stats = '<section class="stats" aria-label="Обзор">' + items.map(([count, label, symbol]) => `<div class="stat"><div class="stat-icon">${icon(symbol, 20)}</div><div><strong>${count}</strong><span>${label}</span></div></div>`).join('') + '</section>';
    return html.replace(/<section class="stats"[^>]*>[\s\S]*?<\/section>/, stats);
  };
  const originalPersonCard = personCard;
  personCard = function(person) {
    let html = originalPersonCard(person).replace('<div class="person-footer">', personalDatesHTML(person, true) + '<div class="person-footer">');
    html = html.replace(/(<button class="favorite-button[\s\S]*?>)[\s\S]*?<\/button>/, '$1' + icon('star', 18) + '</button>');
    if (person.status === 'planned' || person.status === 'done') {
      html = html.replace(/(<div class="cover-tag[^>]*>)<svg[\s\S]*?<\/svg>/, '$1' + icon('flame', 13));
    }
    return html;
  };
  const originalShowDetail = showDetail;
  showDetail = function(id, photo = 0) {
    originalShowDetail(id, photo);
    const person = contactById(id);
    const bottom = mainDialog.querySelector('.detail-bottom');
    if (!person || !bottom) return;
    bottom.insertAdjacentHTML('beforebegin', '<section class="personal-dates-panel"><h3>Личные даты</h3>' + personalDatesHTML(person) + '<p class="form-hint">Указаны вручную. Приложение не рассчитывает цикл.</p></section>');
  };
  const originalEditPerson = editPerson;
  editPerson = function(id = null) {
    originalEditPerson(id);
    const body = mainDialog.querySelector('.dialog-body');
    if (!S.editor || !body) return;
    for (const field of dateFields) S.editor.person[field] ??= '';
    const section = document.createElement('section');
    section.className = 'form-section personal-dates-editor';
    section.innerHTML = '<div class="row spread"><div class="form-section-title">Личные даты · необязательно</div><button type="button" class="text-button" data-clear-personal-dates>Очистить даты</button></div><div class="field-grid">' + dateFields.map(field => `<label${field === 'ovulationDate' ? ' class="full"' : ''}><span class="form-label">${dateLabels[field]}</span><input class="field" name="${field}" type="date" min="1900-01-01" max="2100-12-31" value="${esc(S.editor.person[field])}" aria-describedby="personalDatesHint"></label>`).join('') + '</div><p id="personalDatesHint" class="form-hint">Заполняй только с её согласия. Даты вводятся вручную, приложение ничего не рассчитывает. Любое поле можно оставить пустым.</p>';
    body.insertBefore(section, body.querySelector('#personError'));
  };
  const originalPersonFromForm = personFromForm;
  personFromForm = function(form) {
    const person = originalPersonFromForm(form), data = new FormData(form);
    const dates = Object.fromEntries(dateFields.map(field => [field, String(data.get(field) ?? '').trim()]));
    return Object.assign(person, readPersonalDates(dates));
  };
  // Existing backup format/schema remains unchanged; optional dates survive import.
  const originalNormalizeImportedVault = normalizeImportedVault;
  normalizeImportedVault = function(raw) {
    return originalNormalizeImportedVault(raw).map((person, index) => Object.assign(person, readPersonalDates(raw.contacts[index])));
  };
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-clear-personal-dates]');
    if (!button || !mainDialog.contains(button) || !S.editor) return;
    event.preventDefault();
    const form = button.closest('form');
    for (const field of dateFields) {
      const input = form.elements.namedItem(field);
      if (input) input.value = '';
    }
    S.editor.dirty = true;
  });
})();
