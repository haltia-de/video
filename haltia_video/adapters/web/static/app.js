// SPDX-License-Identifier: GPL-2.0
// The window's pages in a browser. What a page holds, what its menus
// offer and what they ask first come from the core (core/views.py), as
// the window gets them; this draws them the way the window does and keeps
// its own navigation: the five tabs kept as left, a sub-page over them.
const $ = id => document.getElementById(id);
const video = $('video');
const TABS = {home: 'Главная', feed: 'Новое', channels: 'Каналы', bookmarks: 'Закладки', history: 'История'};
// A kept tab older than this is drawn anew (its caches' news, the ages).
const KEEP_MS = 5 * 60 * 1000;
const PAGE = 30;  // what one query of a list brings
// A video whose metadata has not come by then is told as failed. A source
// that stops early leaves the browser retrying the cut response with no
// error event; the PC itself gives FFmpeg's first bytes 20 s (media.py).
const PREPARE_MS = 30 * 1000;
const MEDIA_FAILED = 'Не удалось получить видео с ПК. Повторите или откройте его в сервисе.';
let state = null, events = null, revision = 0;
let tab = 'home', sub = null, prev = null, current = null, mode = 'tabs';
const panes = {};
let refreshTimer = null, refreshBusy = false, refreshAgain = false;
let pendingAction = null, pendingPlay = null, playGeneration = 0, playing = null;
let reportedAt = 0, seeking = false, neighborsGeneration = 0, toastTimer = null, prepareTimer = null;
const preferences = load('haltia-view', {});
const bookmarkFilters = Object.assign({kind: 'all', source: 'all', state: 'all', sort: 'added', reverse: true},
                                      preferences.bookmarks || {});
let bookmarkQuery = '';
const hintsOpen = new Set();

function load(name, fallback) {
  try { return JSON.parse(localStorage.getItem(name)) || fallback; } catch { return fallback; }
}
function savePreferences() {
  preferences.bookmarks = bookmarkFilters;
  try { localStorage.setItem('haltia-view', JSON.stringify(preferences)); } catch { /* private mode */ }
}

// --- small things -----------------------------------------------------------
function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}
function button(text, action, cls) {
  const node = el('button', text, cls);
  node.type = 'button';
  node.onclick = event => { event.stopPropagation(); action(event); };
  return node;
}
function image(url, cls) {
  const node = el('img', null, cls);
  node.alt = ''; node.loading = 'lazy'; node.decoding = 'async';
  if (url) node.src = url;
  return node;
}
function toggle(on, change, label) {
  const node = el('input', null, 'switch');
  node.type = 'checkbox'; node.checked = !!on; node.setAttribute('aria-label', label);
  node.onchange = () => change(node.checked);
  return node;
}
function time(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return '';
  seconds = Math.max(0, Math.floor(seconds));
  const h = Math.floor(seconds / 3600), m = Math.floor(seconds / 60) % 60, s = seconds % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(s).padStart(2, '0');
}
// A service's empty date (0001-01-01, 1970) is no date.
const OLDEST_DATE = 1104537600;  // 2005-01-01
function age(ts) {
  if (!ts || ts < OLDEST_DATE) return '';
  const days = Math.floor((Date.now() / 1000 - ts) / 86400);
  if (days <= 0) return Math.max(1, Math.floor((Date.now() / 1000 - ts) / 3600)) + ' ч назад';
  if (days === 1) return 'вчера';
  if (days < 7) return days + ' ' + plural(days, 'день', 'дня', 'дней') + ' назад';
  if (days < 30) { const w = Math.floor(days / 7); return w + ' ' + plural(w, 'неделю', 'недели', 'недель') + ' назад'; }
  return new Date(ts * 1000).toLocaleDateString('ru-RU');
}
// A channel's description without its links.
const withoutLinks = text => (text || '').replace(/(https?:\/\/|www\.)\S+/g, '').replace(/\s+/g, ' ').trim();
function plural(n, one, few, many) {
  const d = n % 10, t = n % 100;
  return d === 1 && t !== 11 ? one : d >= 2 && d <= 4 && (t < 12 || t > 14) ? few : many;
}
function subscribers(n) {
  if (n == null) return '';
  if (n >= 1000) {
    const [num, unit] = n >= 1e6 ? [n / 1e6, 'млн'] : [n / 1e3, 'тыс.'];
    const text = num >= 100 ? String(Math.floor(num)) : String(Math.round(num * 10) / 10);
    return text.replace('.', ',') + ' ' + unit + ' подписчиков';
  }
  return n + ' ' + plural(n, 'подписчик', 'подписчика', 'подписчиков');
}
function sourceName(id) { return ((state && state.sources || []).find(s => s.id === id) || {}).label || id; }
function identity(kind, obj) {
  return [obj.source, kind, kind === 'playlist' && obj.source === 'vk' ? obj.owner_id || '' : '', obj.id].join('|');
}
function columns() {
  return window.innerWidth <= 600 ? 1 : window.innerHeight > window.innerWidth ? 2 : 3;
}

// --- the PC -----------------------------------------------------------------
async function api(path, body) {
  const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'},
                                      body: JSON.stringify(body)});
  const text = await response.text();
  let data;
  try { data = JSON.parse(text); } catch { data = {error: text}; }
  if (response.status === 401) { needPair(); throw Error('Подключите браузер по коду с ПК.'); }
  if (!response.ok) throw Error(data.error || 'Не удалось выполнить запрос.');
  return data;
}
const query = (view, params = {}) => api('/api/query', {view, params});
async function command(name, params = {}) {
  try { await api('/api/command', {name, params}); scheduleRefresh(); return true; }
  catch (error) { say(error.message); return false; }
}

// --- dialogs, menus, the note, «Вернуть» ------------------------------------
function modal(title, fill) {
  return new Promise(resolve => {
    const box = $('modal-body');
    box.replaceChildren();
    if (title) box.append(el('h2', title));
    const finish = value => { $('modal').hidden = true; resolve(value); };
    fill(box, finish);
    box.append(button('Отмена', () => finish(null)));
    $('modal').hidden = false;
    const first = box.querySelector('button,input');
    if (first) first.focus();
  });
}
$('modal').onclick = event => { if (event.target === $('modal')) $('modal-body').lastChild.click(); };
// A question as the window's dialogs ask it: the text, the act, «Отмена».
const ask = (text, act) => modal(null, (box, finish) => { box.append(el('p', text), button(act, () => finish(true), 'primary')); })
  .then(value => value === true);
// What the core offers (views.actions), carried out by runAction.
async function actionMenu(kind, item, context = {}, handlers = {}, status = '') {
  let actions;
  try { actions = (await query('actions', {kind, item, context})).actions; } catch (error) { say(error.message); return; }
  const chosen = await modal(status || null, (box, finish) => {
    actions.forEach(action => box.append(button(action.title, () => finish(action))));
  });
  if (chosen) await runAction(chosen, handlers);
}
async function runAction(action, handlers = {}) {
  if (action.command) {
    if (action.confirm && !await ask(action.confirm.text, action.confirm.button)) return;
    if (!await command(action.command, action.params)) return;
    if (action.undo) notice(action.undo.notice, () => command(action.undo.command, action.undo.params));
    else if (action.id === 'follow') say('Вы подписались на «' + (action.params.entity.title || '') + '».');
    return;
  }
  const handler = handlers[action.id] || {
    channel: a => openChannel(a.entity),
    add_link: addLink,
  }[action.id];
  if (handler) handler(action);
}
function notice(text, undo) {
  $('toast-text').textContent = text;
  $('toast-button').onclick = () => { $('toast').hidden = true; undo(); };
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 5000);
}
// The line over a page; a spinner beside one that tells of something on
// its way («Загружаю…»): a page that only waits looked stuck.
function say(text, pane = current) {
  if (!pane) return;
  pane.note.replaceChildren();
  if (!text) return;
  if (text.endsWith('…')) pane.note.append(el('span', null, 'spinner'));
  pane.note.append(el('span', text));
}

// --- panes: the kept tabs and the sub-pages ---------------------------------
function makePane() {
  const node = el('section', null, 'pane'), note = el('div', null, 'note'), content = el('div');
  node.append(note, content);
  node.hidden = true;
  $('panes').append(node);
  return {node, note, content, scroll: 0, made: 0, stale: true, lists: [], generation: 0, page: null};
}
function showPane(pane) {
  if (current && current !== pane) {
    current.scroll = window.scrollY;
    current.node.hidden = true;
  }
  current = pane;
  pane.node.hidden = false;
  window.scrollTo(0, pane.scroll);
}
function dropPane(pane) {
  if (!pane || pane === current) return;
  pane.lists.forEach(list => list.stop());
  pane.node.remove();
}
function fresh(pane) { return pane && !pane.stale && Date.now() - pane.made < KEEP_MS; }
function setMode(which, title = 'Видео') {
  mode = which;
  document.body.className = 'mode-' + which;
  $('title').textContent = which === 'tabs' ? TABS[tab] : title;
  $('back').hidden = !(sub || tab !== 'home' || which === 'search');
}
function markTab(key) {
  document.querySelectorAll('#tabs button').forEach(b => b.classList.toggle('current', b.dataset.page === key));
}

function showTab(key, again = false) {
  const left = sub;
  sub = null;
  leave(left);
  tab = key;
  setMode(key === 'bookmarks' && bookmarkQuery ? 'search' : 'tabs');
  $('query').placeholder = key === 'bookmarks' ? 'Поиск в закладках' : 'Найти видео';
  $('query').value = key === 'bookmarks' ? bookmarkQuery : '';
  updateScope();
  markTab(key);
  let pane = panes[key];
  const kept = fresh(pane) && !again;
  const made = !!pane;
  if (!pane) pane = panes[key] = makePane();
  showPane(pane);
  [left && left.pane, prev && prev.pane].forEach(dropPane);
  prev = null;
  if (!kept) draw(pane, {view: key, params: key === 'bookmarks' ? bookmarkParams() : {}}, made);
}
function tabClicked(key) {
  if (!sub && tab === key && mode !== 'search') { window.scrollTo(0, 0); return; }
  showTab(key);
}
// A page over the tabs; ← returns to the tab. The channel a playlist is
// opened from is kept for ← (restoreChannel).
function openSub(view, params, title, replace = false) {
  const left = sub;
  sub = {view, params, title, pane: makePane()};
  if (prev) dropPane(prev.pane);
  prev = null;
  const gone = [];
  if (left && left.view === 'channel') prev = left; else if (left) gone.push(left.pane);
  showPane(sub.pane);
  gone.forEach(dropPane);
  leave(left);
  setMode(view === 'search' ? 'search' : 'title', title);
  if (view === 'settings') markTab(null);
  if (!replace) history.pushState({sub: true}, '');
  draw(sub.pane, sub, false);
}
function restoreChannel(chan) {
  if (!(prev && fresh(prev.pane) && prev.params.entity.source === chan.source && prev.params.entity.id === chan.id)) return false;
  const left = sub;
  sub = prev; prev = null;
  showPane(sub.pane);
  dropPane(left.pane);
  leave(left);
  setMode('title', sub.title);
  return true;
}
function leave(left) {
  if (left && left.view === 'playlist_editor' && left.params.token) {
    api('/api/command', {name: 'playlist_cancel', params: {entity: left.params.entity, token: left.params.token}}).catch(() => {});
  }
  if (pendingAction && pendingAction.page === left) pendingAction = null;
}
function back() {
  if (mode === 'search' && !sub) { closeSearch(); return; }
  if (!sub) { if (tab !== 'home') showTab('home'); return; }
  if (sub.view === 'playlist_editor') {
    const p = sub.params;
    openSub('playlist', {entity: p.entity, chan: p.chan, order: p.changed ? p.order : p.return_order}, p.entity.title, true);
    return;
  }
  if (sub.view === 'playlist' && sub.params.chan && !sub.params.chan.from_tab) {
    const chan = sub.params.chan;
    if (!restoreChannel(chan)) openSub('channel', {entity: chan}, chan.title, true);
    return;
  }
  showTab(tab);
}
function openChannel(chan) { openSub('channel', {entity: chan}, chan.title || 'Канал'); }
function openPlaylist(playlist, chan) {
  openSub('playlist', {entity: playlist, chan: chan || null}, playlist.title || 'Плейлист');
}
function openSearch() {
  setMode('search');
  $('query').placeholder = !sub && tab === 'bookmarks' ? 'Поиск в закладках' : 'Найти видео';
  updateScope();
  $('query').focus();
}
function closeSearch() {
  $('query').value = '';
  setMode(sub ? 'title' : 'tabs', sub && sub.title);
  if (!sub && tab === 'bookmarks' && bookmarkQuery) { bookmarkQuery = ''; showTab('bookmarks', true); }
}
function updateScope() {
  const enabled = state ? state.sources.filter(s => s.enabled) : [];
  const one = enabled.find(s => s.id === state.scope);
  $('scope').textContent = one && enabled.length > 1 ? one.label : 'Все сервисы';
  $('scope').hidden = enabled.length < 2 || (!sub && tab === 'bookmarks');
}
function bookmarkParams() { return Object.assign({query: bookmarkQuery}, bookmarkFilters); }

// --- drawing a page ---------------------------------------------------------
// Drawn again (keep), a page's lists are made as far as they were and it
// stays where it was scrolled to.
async function draw(pane, page, keep) {
  const generation = ++pane.generation;
  const counts = keep ? pane.lists.map(list => list.count) : [];
  const scroll = pane === current ? window.scrollY : pane.scroll;
  pane.made = Date.now(); pane.stale = false; pane.page = page;
  const lists = [];
  const ctx = {pane, page, generation, lists, counts, alive: () => pane.generation === generation};
  try {
    const root = await (PAGES[page.view] || PAGES.list)(ctx);
    if (!ctx.alive()) { lists.forEach(list => list.stop()); return; }
    pane.lists.forEach(list => list.stop());
    pane.lists = lists;
    pane.content.replaceChildren(root);
    if (keep) { if (pane === current) window.scrollTo(0, scroll); else pane.scroll = scroll; }
    else if (pane === current) window.scrollTo(0, 0);
    if (ctx.after) requestAnimationFrame(ctx.after);
  } catch (error) {
    if (!ctx.alive()) return;
    say(error.message, pane);
    if (page.view === 'playlist_editor') pane.note.append(button('Загрузить свежий вариант', () => draw(pane, page, false)));
  }
}
// A list made as it is scrolled to: a page at first (or as far as before),
// the next when its end comes near the window, a spinner while it comes.
async function lazyList(ctx, view, params, make, container) {
  const want = Math.max(PAGE, ctx.counts.shift() || 0);
  const first = await query(view, Object.assign({}, params, {offset: 0, count: want}));
  const list = {count: 0, next: first.next, busy: false, observer: null, stop() { if (this.observer) this.observer.disconnect(); }};
  const add = items => { items.forEach(item => container.append(make(item))); list.count += items.length; };
  add(first.items || []);
  const end = el('div', null, 'end');
  if (list.next != null) {
    end.append(el('div', null, 'spinner more-spinner'));
    list.observer = new IntersectionObserver(async entries => {
      if (!entries.some(e => e.isIntersecting) || list.busy || list.next == null || !ctx.alive()) return;
      list.busy = true;
      try {
        const more = await query(view, Object.assign({}, params, {offset: list.next, count: PAGE}));
        if (!ctx.alive()) return;
        add(more.items || []);
        list.next = more.next;
      } catch (error) { say(error.message, ctx.pane); list.next = null; }
      finally {
        list.busy = false;
        if (list.next == null) { list.stop(); end.replaceChildren(); }
        else { list.observer.unobserve(end); list.observer.observe(end); }  // still in view: the next at once
      }
    }, {rootMargin: '0px 0px 150% 0px'});
    requestAnimationFrame(() => list.observer && list.observer.observe(end));
  }
  ctx.lists.push(list);
  return {first, end};
}
// Rows already here (the order editor's whole playlist) made as scrolled to.
function localList(ctx, items, make, container, batch = 60) {
  let made = Math.min(items.length, Math.max(batch, ctx.counts.shift() || 0));
  items.slice(0, made).forEach(item => container.append(make(item)));
  const list = {count: made, observer: null, stop() { if (this.observer) this.observer.disconnect(); }};
  const end = el('div');
  if (made < items.length) {
    list.observer = new IntersectionObserver(entries => {
      if (!entries.some(e => e.isIntersecting)) return;
      items.slice(made, made + batch).forEach(item => container.append(make(item)));
      made = list.count = Math.min(items.length, made + batch);
      if (made >= items.length) list.stop(); else { list.observer.unobserve(end); list.observer.observe(end); }
    }, {rootMargin: '0px 0px 150% 0px'});
    requestAnimationFrame(() => list.observer && list.observer.observe(end));
  }
  ctx.lists.push(list);
  return end;
}
function section(root, title, action, end) {
  const row = el('div', null, 'section');
  row.append(el('h2', title));
  [action, end].forEach(node => { if (node) row.append(node); });
  root.append(row);
}

// --- cards and rows ---------------------------------------------------------
// Each screen shows cards or rows, as last chosen for it in this browser (a
// phone and a tablet may want them apart); Каналы and Закладки were lists.
const layouts = load('haltia-layouts', {});
const LISTED_FIRST = ['channels', 'found_channels', 'bookmarks'];
function listed(screen) { return (layouts[screen] || (LISTED_FIRST.includes(screen) ? 'list' : 'grid')) === 'list'; }
function layoutButton(screen) {
  const rows = listed(screen);
  const node = button(rows ? '▦' : '☰', () => {
    layouts[screen] = rows ? 'grid' : 'list';
    try { localStorage.setItem('haltia-layouts', JSON.stringify(layouts)); } catch { /* private mode */ }
    draw(current, current.page, true);
  }, 'layout');
  node.setAttribute('aria-label', rows ? 'Карточками' : 'Списком');
  return node;
}
// A row over a screen's content: what it starts with, the switch at its end.
function layoutRow(screen, ...start) {
  const row = el('div', null, 'tools');
  start.forEach(node => row.append(node));
  row.append(el('span', null, 'grow'), layoutButton(screen));
  return row;
}
function listBox(screen) { return el('div', null, listed(screen) ? 'rows' : 'grid'); }
function star(kind, obj, saved, cls = 'pic') {
  const node = button(saved ? '★' : '☆', () => {
    if (saved) bookmarkMenu({key: identity(kind, obj)});
    else command('bookmark_add', {kind, entity: obj});
  }, cls + (saved ? ' saved' : ''));
  node.setAttribute('aria-label', saved ? 'Действия с закладкой' : 'В закладки');
  return node;
}
// A tap; ⋮ and a long press open the same menu.
function pressable(node, tap, hold) {
  let timer = null, held = false, x = 0, y = 0;
  const cancel = () => { clearTimeout(timer); timer = null; node.classList.remove('pressed'); };
  node.addEventListener('pointerdown', event => {
    if (event.button || event.target.closest('button')) return;
    held = false; x = event.clientX; y = event.clientY;
    node.classList.add('pressed');
    timer = setTimeout(() => { held = true; cancel(); hold(); }, 600);
  });
  node.addEventListener('pointermove', event => { if (Math.hypot(event.clientX - x, event.clientY - y) > 10) cancel(); });
  ['pointerup', 'pointercancel', 'pointerleave'].forEach(name => node.addEventListener(name, cancel));
  node.addEventListener('contextmenu', event => { event.preventDefault(); if (!held) { held = true; cancel(); hold(); } });
  node.addEventListener('click', () => { if (held) { held = false; return; } tap(); });
  node.addEventListener('keydown', event => { if (event.key === 'Enter') tap(); });
}
// What any screen lists — a video, a playlist, a channel, a bookmark — built
// alike, as the window builds it: a card (its picture with ☆ and ⋮ on the
// corner, a mark on the other; the title, a line and an action under them),
// or, row, a row of a list (the picture on the left, the title and the line
// beside it, then the action, ☆ and ⋮). A channel's square picture sits in
// the same 16:9 place (square).
function tile(row, o) {
  const node = el('article', null, 'card' + (row ? ' rowcard' : ''));
  node.tabIndex = 0;
  const picture = el('div', null, 'picture' + (o.square ? ' square' : '') + (o.dim ? ' done' : ''));
  picture.append(image(o.thumb));
  if (o.progress) {
    const strip = el('div', null, 'strip'), bar = el('i');
    bar.style.width = Math.round(o.progress * 100) + '%';
    strip.append(bar); picture.append(strip);
  }
  const more = button('⋮', () => o.menu(), row ? 'star' : 'pic');
  more.setAttribute('aria-label', 'Действия');
  const ends = [o.star, more].filter(Boolean);
  const text = el('div', null, 'text');
  text.append(el('h3', o.title), el('div', o.line, 'meta'));
  if (row) {
    if (o.mark) node.append(o.mark);
    node.append(picture, text);
    if (o.action) node.append(o.action);
    node.append(...ends);
  } else {
    const corner = el('div', null, 'corner');
    corner.append(...ends);
    picture.append(corner);
    if (o.mark) { const left = el('div', null, 'corner left'); left.append(o.mark); picture.append(left); }
    node.append(picture, text);
    if (o.action) { o.action.classList.add('go'); node.append(o.action); }
  }
  pressable(node, o.tap, o.menu);
  return node;
}
function card(item, row, lesson = null, hideChannel = false) {
  let mark = null;
  if (lesson) {
    const done = !!item.task_done;
    mark = button(done ? '✓' : '☐', () => command('playlist_task', {entity: lesson, video: item, on: !done}),
                  'pic' + (done ? ' done' : ''));
    mark.setAttribute('aria-label', done ? 'Выполнено: снять отметку' : 'Отметить выполненным');
  }
  // On a channel's page its name, in a playlist its name in each title, are
  // what the page says already.
  const node = tile(row, {title: item.label || item.title, thumb: item.thumb, progress: item.progress,
    line: [hideChannel || item.hide_channel ? '' : item.channel, time(item.duration), age(item.published)].filter(Boolean).join(' · '),
    tap: () => play(item), menu: () => actionMenu('video', item), star: star('video', item, item.bookmarked, row ? 'star' : 'pic'),
    mark, dim: lesson && item.task_done});
  node.dataset.video = item.id;
  if (item.entry_id) node.dataset.entry = item.entry_id;
  return node;
}
function grid(items, screen, lesson, hideChannel) {
  const rows = listed(screen), box = listBox(screen);
  items.forEach(item => box.append(card(item, rows, lesson, hideChannel)));
  return box;
}
// A playlist: the service's cover (or, begun, the video it stopped at), its
// name and size; begun, where it stopped and «Продолжить».
function playlistCard(playlist, chan, row, cursor = null, saved = false) {
  const obj = Object.assign({}, playlist, {owner_title: chan.title});
  const bits = [];
  if (playlist.count) bits.push(playlist.count + ' видео');
  if (cursor) bits.push([cursor.video.label || cursor.video.title, time(cursor.video.pos)].filter(Boolean).join(' '));
  return tile(row, {title: playlist.title || 'Плейлист', line: bits.join(' · '),
    thumb: playlist.thumb || (cursor && cursor.video.thumb), progress: cursor && cursor.video.progress,
    tap: () => openPlaylist(playlist, chan), menu: () => actionMenu('playlist_item', obj),
    star: star('playlist', obj, saved, row ? 'star' : 'pic'),
    action: cursor ? button('▶ Продолжить', () => { openPlaylist(playlist, chan); play(null, true); }, 'primary') : null});
}
function tiles(cursors, screen) {
  const rows = listed(screen), box = listBox(screen);
  cursors.forEach(c => box.append(playlistCard(c.playlist, c.chan, rows, c, c.bookmarked)));
  return box;
}
function channelLine(chan) {
  return [state.sources.length > 1 ? sourceName(chan.source) : '', chan.handle || '',
          subscribers(chan.subscribers), chan.latest ? 'новое видео ' + age(chan.latest) : ''].filter(Boolean).join(' · ');
}
// A channel: its picture, name and what is known of it; ⋮ follows or unfollows it.
function channelCard(chan, row, action = null) {
  return tile(row, {title: chan.title, line: channelLine(chan), thumb: chan.thumb, square: true,
    tap: () => openChannel(chan), menu: () => actionMenu('channel', chan),
    star: star('channel', chan, chan.bookmarked, row ? 'star' : 'pic'), action});
}
function unfollowButton(chan, title = 'Отписаться', cls = 'drop') {
  const node = button(title, async () => {
    const actions = (await query('actions', {kind: 'channel', item: chan})).actions;
    const action = actions.find(a => a.id === 'unfollow');
    if (action) await runAction(action);
  }, cls);
  node.setAttribute('aria-label', 'Отписаться');
  return node;
}
function followButton(chan) {
  return chan.followed ? unfollowButton(chan) : button('Подписаться', () => command('follow', {entity: chan}), 'add');
}
function bookmarkStatus(saved) {
  const links = Object.values(saved.links || {});
  if (!links.length) return ['', 'Только на этом устройстве'];
  const states = links.map(l => l.state);
  const notes = Object.entries(saved.links).map(([aid, l]) => (l.account || aid) + ' · ' +
    (l.state === 'error' ? l.error || 'Ошибка синхронизации' : l.state === 'pending' ? 'Ожидает отправки' : 'Синхронизировано'));
  return [states.includes('error') ? '☁ !' : states.includes('pending') ? '☁ ↻' : '☁ ✓', notes.join('\n')];
}
function bookmarkMenu(saved) { return actionMenu('bookmark', {key: saved.key}, {}, {}, saved.links ? bookmarkStatus(saved)[1] : ''); }
// A bookmark, as what it keeps is shown elsewhere; ⋮ is its own menu.
function bookmarkCard(saved, row) {
  const obj = saved.object, kind = saved.kind;
  const info = [{video: 'Видео', playlist: 'Плейлист', channel: 'Канал'}[kind], sourceName(obj.source),
                obj.channel || obj.owner_title, bookmarkStatus(saved)[0]].filter(Boolean).join(' · ');
  return tile(row, {title: (saved.pin !== undefined ? '📌 ' : '') + obj.title, line: obj.unavailable || info,
    thumb: obj.thumb, square: kind === 'channel', progress: kind === 'video' ? obj.progress : null,
    tap: () => openBookmark(saved), menu: () => bookmarkMenu(saved),
    star: star(kind, obj, true, row ? 'star' : 'pic')});
}
function openBookmark(saved) {
  const obj = saved.object;
  if (!state.sources.some(s => s.id === obj.source)) { say('Этот сервис сейчас недоступен. Закладка сохранена.'); return; }
  if (saved.kind === 'video') play(obj);
  else if (saved.kind === 'channel') openChannel(obj);
  // Back leads to Закладки: a YouTube playlist may name no channel.
  else openPlaylist(obj, {source: obj.source, id: obj.owner_id || '', title: obj.owner_title || 'Канал плейлиста', from_tab: true});
}

// --- the pages --------------------------------------------------------------
const PAGES = {
  async home(ctx) {
    const root = el('div'), result = await query('home'), row = ctx.pane.columns = columns();
    if (!result.sections.length) { say(result.loading ? 'Загружаю…' : '', ctx.pane); root.append(layoutRow('home')); }
    else say('', ctx.pane);
    result.sections.forEach((s, n) => {
      const more = s.more && (s.always || s.total > row) ? button('Все →', () => openMore(s.more)) : null;
      // The switch at the top right, as on every screen: in the first section's row.
      section(root, s.title, more, n === 0 ? layoutButton('home') : null);
      if (s.hint) root.append(el('p', s.hint, 'hint'));
      else if (s.kind === 'playlists') root.append(tiles(s.items.slice(0, row), 'home'));
      else root.append(grid(s.items.slice(0, row), 'home'));
    });
    return root;
  },
  async feed(ctx) {
    const root = el('div'), box = listBox('feed');
    root.append(layoutRow('feed'));
    const {first, end} = await lazyList(ctx, 'feed', {}, item => card(item, listed('feed')), box);
    say(!first.channels ? 'Здесь будут новые видео каналов, на которые вы подписаны. Чтобы подписаться на канал, ' +
        'нажмите ⋮ на его видео или найдите канал на странице «Каналы».' :
        first.items.length ? '' : first.loading ? 'Загружаю новые видео…' :
        first.failed ? 'Не удалось загрузить: нет связи?' : 'У каналов пока нет видео.', ctx.pane);
    root.append(box, end);
    return root;
  },
  async history(ctx) {
    const root = el('div'), box = listBox('history');
    root.append(layoutRow('history'));
    const {first, end} = await lazyList(ctx, 'history', {}, item => card(item, listed('history')), box);
    say(first.items.length ? '' : 'Здесь будут видео, которые вы смотрели.', ctx.pane);
    root.append(box, end);
    return root;
  },
  async channels(ctx) {
    const root = el('div'), box = listBox('channels'), finder = channelFinder('');
    finder.append(layoutButton('channels'));
    root.append(finder);
    // Unfollowing is in ⋮, as everything else a channel offers.
    const {first, end} = await lazyList(ctx, 'channels', {}, chan => channelCard(chan, listed('channels')), box);
    say(first.items.length ? '' : 'Подписок пока нет. Найдите канал по названию или вставьте ссылку на него.', ctx.pane);
    root.append(box, end);
    return root;
  },
  async bookmarks(ctx) {
    const root = el('div'), f = bookmarkFilters;
    // One row: the kinds as chips, a chip for any other filter on (a tap
    // clears it), and ⋮ for the rest.
    const chips = el('div', null, 'chips');
    [['all', 'Все'], ...state.bookmark_kinds].forEach(([value, title]) =>
      chips.append(button(title, () => setBookmarkFilter('kind', value), 'chip' + (f.kind === value ? ' on' : ''))));
    const names = Object.assign({local: 'Локальные', synced: 'Синхронизируемые'},
                                Object.fromEntries(state.sources.map(s => [s.id, s.label])));
    ['source', 'state'].forEach(field => {
      if (f[field] !== 'all') chips.append(button((names[f[field]] || f[field]) + ' ✕', () => setBookmarkFilter(field, 'all'), 'chip on'));
    });
    const more = button('⋮', () => actionMenu('bookmarks', {}, f, {filter: a => setBookmarkFilter(a.field, a.value)}));
    more.setAttribute('aria-label', 'Ещё');
    const layout = layoutButton('bookmarks');
    layout.classList.add('end');
    chips.append(layout, more);
    root.append(chips);
    const box = listBox('bookmarks');
    const {first, end} = await lazyList(ctx, 'bookmarks', bookmarkParams(), saved => bookmarkCard(saved, listed('bookmarks')), box);
    say(first.items.length ? '' : 'Закладок пока нет или они не подходят под фильтры.', ctx.pane);
    root.append(box, end);
    return root;
  },
  async my_playlists(ctx) {
    const root = el('div'), box = listBox('my_playlists');
    root.append(layoutRow('my_playlists'));
    const {first, end} = await lazyList(ctx, 'my_playlists', {}, c => playlistCard(c.playlist, c.chan, listed('my_playlists'), c, c.bookmarked), box);
    say(first.items.length ? '' : 'Здесь будут плейлисты, которые вы начали смотреть.', ctx.pane);
    root.append(box, end);
    return root;
  },
  async source_home(ctx) {
    const root = el('div'), box = listBox('source_home');
    root.append(layoutRow('source_home'));
    const {first, end} = await lazyList(ctx, 'source_home', ctx.page.params, item => card(item, listed('source_home')), box);
    say(first.items.length ? '' : first.loading ? 'Загружаю…' : '', ctx.pane);
    root.append(box, end);
    return root;
  },
  async search(ctx) {
    const p = ctx.page.params, root = el('div'), kind = p.kind === 'channels' ? 'channels' : 'search';
    if (kind === 'channels') { const finder = channelFinder(p.query); finder.append(layoutButton('found_channels')); root.append(finder); }
    const result = await query('search', {query: p.query, kind, source: state.scope || 'all', sort: state.sort});
    if (result.can_sort) {
      // The two orders side by side, the one on lit: one button named after
      // the order on did not tell a state from what a tap would do.
      const seg = el('div', null, 'seg');
      [['date', 'Сначала новые'], ['relevance', 'Сначала подходящие']].forEach(([value, title]) =>
        seg.append(button(title, () => { if (state.sort !== value) command('sort', {sort: value}); }, state.sort === value ? 'current' : '')));
      root.append(layoutRow('search', seg));
    } else if (kind === 'search') root.append(layoutRow('search'));
    result.sections.forEach(s => {
      if (s.label) section(root, s.label);
      if (kind === 'search') root.append(grid(s.items, 'search'));
      else {
        const rows = listBox('found_channels'), listedNow = listed('found_channels');
        s.items.forEach(chan => rows.append(channelCard(chan, listedNow, followButton(chan))));
        root.append(rows);
      }
    });
    say(result.note || (result.loading ? 'Ищу…' : ''), ctx.pane);
    return root;
  },
  async channel(ctx) {
    const page = ctx.page, chan = page.params.entity, root = el('div');
    const view = page.params.tab || 'videos';
    const box = listBox('channel');
    const {first, end} = await lazyList(ctx, 'channel', {entity: chan}, item => card(item, listed('channel'), null, true), box);
    const known = first.known || chan;
    const head = el('div', null, 'head'), text = el('div', null, 'text');
    text.append(el('div', known.title, 'name'));
    const bits = [state.sources.length > 1 ? sourceName(known.source) : '', known.handle || '',
                  subscribers(known.subscribers), known.latest ? 'новое видео ' + age(known.latest) : ''].filter(Boolean);
    if (bits.length) text.append(el('div', bits.join(' · '), 'sub'));
    const mark = star('channel', known, first.bookmarked);
    mark.className = mark.className.replace('pic', 'star');
    head.append(image(known.thumb, 'avatar'), text,
                first.followed ? unfollowButton(known, 'Отписаться') : button('Подписаться', () => command('follow', {entity: known}), 'add'),
                mark);
    root.append(head);
    if (first.has_playlists) {
      // Videos first: a channel's dozen playlists used to push them off the screen.
      const seg = el('div', null, 'seg');
      [['videos', 'Видео'], ['playlists', 'Плейлисты' + (first.playlists.length ? ' · ' + first.playlists.length : '')]].forEach(([k, title]) =>
        seg.append(button(title, () => { if (k !== view) { page.params.tab = k; draw(ctx.pane, page, false); } },
                          k === view ? 'current' : '')));
      root.append(layoutRow(view === 'playlists' ? 'channel_playlists' : 'channel', seg));
      if (view === 'playlists') {
        say(first.playlists.length ? '' : first.loading ? 'Загружаю плейлисты…' : 'У канала нет плейлистов.', ctx.pane);
        const albums = listBox('channel_playlists'), rows = listed('channel_playlists');
        first.playlists.forEach(a => albums.append(playlistCard(a, chan, rows, null, a.bookmarked)));
        root.append(albums);
        return root;
      }
    } else root.append(layoutRow('channel'));
    say(first.items.length ? '' : first.failure || (first.loading ? 'Загружаю…' : 'У канала пока нет видео.'), ctx.pane);
    root.append(box, end);
    return root;
  },
  async playlist(ctx) {
    const page = ctx.page, p = page.params, root = el('div');
    const result = await query('playlist', {entity: p.entity, order: p.order, offset: p.offset || 0});
    p.order = result.order;
    page.result = result;
    // One row over the cards: the order, «Продолжить» and ⋮ for the rest.
    const tools = el('div', null, 'tools');
    const orders = el('select');
    orders.setAttribute('aria-label', 'Порядок плейлиста');
    state.playlist_orders.forEach(([id, title]) => { const o = el('option', title); o.value = id; orders.append(o); });
    orders.value = result.order;
    orders.onchange = () => { p.order = orders.value; p.offset = 0; p.previous = []; draw(ctx.pane, page, false); };
    tools.append(orders);
    const resume = result.play;
    if (resume) {
      const go = button(['▶ ' + resume.caption, resume.title, time(resume.pos)].filter(Boolean).join(' · '),
                        () => play(null, true, resume.action === 'first'), 'primary grow');
      go.disabled = !resume.enabled;
      tools.append(go);
    } else tools.append(el('span', null, 'grow'));
    const more = button('⋮', () => actionMenu('playlist', Object.assign({}, p.entity, {owner_title: (p.chan || {}).title}),
                                               {order: result.order}, playlistHandlers(page)));
    more.setAttribute('aria-label', 'Ещё');
    tools.append(layoutButton('playlist'), more);
    root.append(tools);
    const bits = [];
    if (result.order === 'lesson') bits.push('Выполнено ' + result.tasks.done + ' из ' + result.tasks.total);
    if (resume && resume.note) bits.push(resume.note);
    if (bits.length) root.append(el('p', bits.join(' · '), 'hint'));
    if (!result.ready) say(result.failure || (result.order !== 'vk' ? 'Загружаю весь плейлист…' : 'Загружаю…'), ctx.pane);
    else say(result.items.length ? '' : 'На этой странице нет доступных видео.', ctx.pane);
    root.append(grid(result.items || [], 'playlist', result.order === 'lesson' ? p.entity : null));
    const nav = el('div', null, 'pager'), previous = p.previous || [];
    if (previous.length) nav.append(button('← Предыдущие', () => {
      p.offset = previous[previous.length - 1]; p.previous = previous.slice(0, -1); draw(ctx.pane, page, false);
    }));
    if (result.next != null) nav.append(button('Следующие →', () => {
      p.previous = previous.concat([p.offset || 0]); p.offset = result.next; draw(ctx.pane, page, false);
    }));
    root.append(nav);
    if (p.target) {
      const target = p.target;
      ctx.after = () => {
        const node = Array.from(root.querySelectorAll('[data-video]')).find(n => target.entry ? n.dataset.entry === target.entry : n.dataset.video === target.id);
        if (node) { node.classList.add('located'); node.focus({preventScroll: true}); node.scrollIntoView({block: 'center'}); }
        delete p.target;
      };
    }
    return root;
  },
  async playlist_editor(ctx) {
    const page = ctx.page, p = page.params, root = el('div');
    let result;
    try {
      result = await query('playlist_editor', {entity: p.entity, order: p.order, token: p.token, count: 2000});
    } catch (error) { delete p.token; throw error; }
    if (result.loading || result.failed) {
      say(result.failure || 'Загружаю плейлист…', ctx.pane);
      return root;
    }
    p.token = result.token;
    const items = page.items = result.items;
    const picked = items.find(v => v.entry_id === p.pick);
    if (!picked) p.pick = null;
    const tools = el('div', null, 'tools');
    let hint;
    if (picked) {
      hint = 'Куда поставить «' + picked.title + '»? Нажмите видео, перед которым оно встанет.';
      tools.append(button('В начало', () => editorPlace(page, picked.minimum)), button('В конец', () => editorPlace(page, picked.maximum)),
                   button('Отмена', () => { p.pick = null; draw(ctx.pane, page, true); }));
    } else {
      hint = '↑ и ↓ передвигают видео на одно место. Чтобы перенести дальше, нажмите видео, затем то, перед которым его ' +
             'поставить. Изменения сохраняются сразу.' + (p.order === 'lesson' ? ' Переставлять можно внутри одного номера урока.' : '');
      if ((p.undo || []).length) tools.append(button('↶ Отменить', () => editorUndo(page)));
      if (result.manual) tools.append(button(p.order === 'custom' ? 'Вернуть порядок сервиса' : 'Сбросить поправки', () => editorReset(page)));
    }
    root.append(el('p', hint, 'hint'));
    if (tools.childElementCount) root.append(tools);
    say(items.length ? '' : 'В плейлисте нет доступных видео.', ctx.pane);
    const rows = el('div', null, 'rows');
    const end = localList(ctx, items, item => {
      // ↑ and ↓ step it, as Настройки of the shell orders the panel; a tap
      // on the row picks it for a longer way.
      const row = el('div', null, 'row' + (item.entry_id === p.pick ? ' picked' : ''));
      row.dataset.entry = item.entry_id;
      const open = button('', () => editorTap(page, item), 'open');
      open.append(el('span', item.position + 1, 'num'), image(item.thumb, 'mini'), el('span', item.title, 'name text'));
      const up = button('↑\uFE0E', () => editorStep(page, item, -1), 'step'), down = button('↓\uFE0E', () => editorStep(page, item, 1), 'step');
      up.setAttribute('aria-label', 'Выше'); down.setAttribute('aria-label', 'Ниже');
      up.disabled = item.position <= item.minimum; down.disabled = item.position >= item.maximum;
      row.append(open, up, down);
      return row;
    }, rows);
    root.append(rows, end);
    return root;
  },
  async settings(ctx) {
    const root = el('div');
    section(root, 'Сервисы');
    // The accounts are the PC's: a browser signs in to nothing of its own.
    root.append(el('p', 'Аккаунты сервисов — общие с Видео на ПК: войти достаточно один раз, здесь или на ПК, ' +
                    'и вход действует для всех подключённых устройств.', 'hint'));
    state.sources.forEach(src => root.append(sourceSettings(src)));
    root.append(el('p', 'Поиск идёт во всех включённых сервисах, первыми — результаты YouTube. Кнопка рядом с полем поиска ' +
                    'оставляет один сервис.', 'hint'));
    section(root, 'Главная');
    const sections = state.home_sections;
    sections.forEach((s, n) => {
      const row = el('div', null, 'row');
      row.append(el('div', s.title, 'name text wrap'));
      const up = button('↑\uFE0E', () => homeStep(n, -1), 'step'), down = button('↓\uFE0E', () => homeStep(n, 1), 'step');
      up.setAttribute('aria-label', 'Выше'); down.setAttribute('aria-label', 'Ниже');
      up.disabled = n === 0; down.disabled = n === sections.length - 1;
      row.append(up, down, toggle(s.on, on => command('home_sections', {sections: sections.map(e => ({id: e.id, on: e.id === s.id ? on : e.on}))}), s.title));
      root.append(row);
    });
    section(root, 'Видео');
    root.append(setting('Shorts и клипы', 'Короткие вертикальные видео YouTube и VK Видео: в поиске, на Главной, ' +
                        'в подписках и на каналах.', state.shorts, on => command('shorts', {on})));
    root.append(setting('Спрашивать перед удалением', 'Отписка от канала и удаление закладки. Если выключено, внизу на несколько ' +
                        'секунд появится кнопка «Вернуть». Сброс прогресса и выход из аккаунта спрашивают всегда.',
                        state.confirm, on => command('confirm', {on})));
    say('', ctx.pane);
    return root;
  },
};
PAGES.list = PAGES.history;

function openMore(more) {
  if (more.view === 'my_playlists') openSub('my_playlists', {}, 'Мои плейлисты');
  else if (more.view === 'source_home') openSub('source_home', {source: more.source}, sourceName(more.source));
  else showTab(more.view);
}
function channelFinder(text) {
  const form = el('form', null, 'tools'), input = el('input');
  input.placeholder = 'Найти канал, ссылка или @имя'; input.value = text || ''; input.className = 'grow';
  input.setAttribute('aria-label', 'Найти канал');
  const go = el('button', 'Найти', 'add'); go.type = 'submit';
  form.append(input, go);
  form.onsubmit = event => {
    event.preventDefault();
    const q = input.value.trim();
    if (q) openSub('search', {query: q, kind: 'channels'}, 'Поиск каналов', sub && sub.view === 'search');
  };
  return form;
}
function setBookmarkFilter(field, value) {
  bookmarkFilters[field] = value;
  savePreferences();
  showTab('bookmarks', true);
}
// Under «Вход выполнен: …», which names the account already.
function syncRow(src) {
  const row = el('div', null, 'top');
  row.append(el('div', src.sync.note, 'desc grow'));
  if (src.sync.kinds && src.sync.kinds.length) row.append(toggle(src.sync.enabled, on => command('sync_enabled', {source: src.id, on}), 'Синхронизировать закладки'));
  return row;
}
function setting(title, text, on, change) {
  const box = el('div', null, 'setting'), top = el('div', null, 'top');
  top.append(el('div', title, 'name'), toggle(on, change, title));
  box.append(top, el('div', text, 'desc'));
  return box;
}
function homeStep(n, step) {
  const sections = state.home_sections.map(e => ({id: e.id, on: e.on}));
  [sections[n], sections[n + step]] = [sections[n + step], sections[n]];
  command('home_sections', {sections});
}
function sourceSettings(src) {
  const box = el('div', null, 'setting'), top = el('div', null, 'top');
  top.append(el('div', src.label, 'name'), toggle(src.enabled, on => command('source_enabled', {source: src.id, on}), src.label));
  box.append(top);
  const account = src.account;
  if (!account) return box;
  const kind = account.kind, buttons = el('div', null, 'tools');
  const sign = (action, extra = {}) => command('signin', Object.assign({source: src.id, action}, extra));
  let note = null;
  if (kind === 'in') {
    box.append(el('div', 'Вход выполнен: ' + account.name, 'desc'));
    buttons.append(button('Синхронизировать подписки', () => command('import_subscriptions', {source: src.id}), 'add'),
                   button('Выйти', async () => { if (await ask(src.label, 'Выйти из аккаунта')) sign('logout'); }, 'drop'));
    note = src.note;
    if (src.sync) box.append(syncRow(src));
  } else if (kind === 'waiting') {
    const row = el('div', null, 'tools'), qr = image('/qr?source=' + encodeURIComponent(src.id), 'qr');
    const text = el('div', null, 'grow');
    const link = el('a', account.url.replace('https://', '')); link.href = account.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    text.append(el('p', 'Наведите камеру телефона на код или откройте'), link,
                el('p', 'и подтвердите вход. Если VK покажет код, введите его здесь.'));
    row.append(qr, text);
    box.append(row);
    buttons.append(button('VK показал код', () => sign('enter_code'), 'add'));
  } else if (kind === 'code') {
    box.append(el('div', 'Введите код, который VK показал на телефоне после перехода по QR-коду.', 'desc'));
    const code = el('input'); code.inputMode = 'numeric'; code.placeholder = 'Код от VK';
    box.append(code);
    buttons.append(button('Подтвердить', () => sign('submit', {code: code.value}), 'add'), button('Получить новый QR-код', () => sign('begin')));
    note = account.note;
  } else if (kind === 'locked') {
    note = account.note;
    buttons.append(button('Открыть связку ключей', () => sign('begin'), 'add'));
  } else {
    if (src.login === 'cookies') {
      // Folded: a file path frightens more than it helps someone who
      // only wants to watch.
      const open = hintsOpen.has(src.id), how = button((open ? '▾ ' : '▸ ') + 'Как войти в ' + src.label + '?', () => {
        if (hintsOpen.has(src.id)) hintsOpen.delete(src.id); else hintsOpen.add(src.id);
        draw(current, current.page, true);
      });
      box.append(how);
      if (open) {
        box.append(el('div', src.hint, 'desc'));
        const file = el('input'); file.type = 'file'; file.accept = '.txt'; file.setAttribute('aria-label', 'Файл cookies.txt');
        file.onchange = async () => {
          if (!file.files.length) return;
          try {
            const response = await fetch('/api/cookies?source=' + encodeURIComponent(src.id), {method: 'POST', body: file.files[0]});
            if (!response.ok) throw Error('Не удалось передать cookies.');
            scheduleRefresh();
          } catch (error) { say(error.message); }
        };
        box.append(el('div', 'Или передайте на ПК файл cookies.txt из этого браузера:', 'desc'), file);
      }
    }
    note = {getting: 'Получаю код…', checking: 'Проверяю…', submitting: 'Проверяю код…', confirming: 'Завершаю вход…'}[kind] || account.note;
    if (kind === 'out' || kind === 'failed') {
      const browsers = src.browsers || [];
      browsers.forEach(([id, name]) => buttons.append(button('Войти через ' + name + ' на ПК', () => command('signin', {source: src.id, browser: id}), 'add')));
      // One main action: with a browser to sign in through, the file is the other way.
      buttons.append(button(src.login === 'code' ? 'Войти по QR-коду' : 'Проверить файл', () => sign('begin'), browsers.length ? 'drop' : 'add'));
    }
  }
  if (note) box.append(el('div', note, 'desc'));
  if (buttons.childElementCount) box.append(buttons);
  return box;
}

// --- a playlist's own actions -----------------------------------------------
function playlistHandlers(page) {
  const p = page.params;
  return {
    edit_order: a => openSub('playlist_editor', {entity: p.entity, chan: p.chan, order: a.order, return_order: a.return_order, undo: []},
                             a.order === 'lesson' ? 'Порядок уроков' : 'Мой порядок'),
    locate: () => playlistAction(page, 'locate'),
    reset: a => playlistAction(page, 'reset_preview', a.what),
  };
}
async function playlistAction(page, action, what = 'progress') {
  pendingAction = {action, what, page};
  await attemptAction();
}
async function attemptAction() {
  const pending = pendingAction;
  if (!pending || pending.page !== sub) return;
  const p = pending.page.params;
  try {
    const result = await query(pending.action, {entity: p.entity, order: p.order, what: pending.what});
    if (pendingAction !== pending) return;
    if (result.loading) { say('Загружаю плейлист…'); return; }
    pendingAction = null;
    if (result.failed) throw Error('Не удалось проверить плейлист. Прогресс сохранён.');
    if (pending.action === 'locate') {
      if (!result.video) throw Error('В плейлисте нет доступных видео. Прогресс сохранён.');
      p.offset = result.offset; p.previous = result.previous || [];
      p.target = {id: result.target, entry: result.entry_id};
      draw(current, pending.page, false);
      return;
    }
    const text = pending.what === 'tasks' ? 'Снять все отметки выполнения этого плейлиста?' :
      pending.what === 'order' ? 'Сбросить ручной порядок и поправки внутри уроков этого плейлиста?' :
      'Будут сброшены последнее видео и позиции просмотра всех видео плейлиста, также в других плейлистах, где они ' +
      'встречаются. Закладки, история и сортировка сохранятся.';
    const act = {tasks: 'Сбросить выполнение', order: 'Сбросить ручной порядок'}[pending.what] || 'Сбросить прогресс';
    if (await ask(text, act)) {
      await api('/api/command', {name: 'playlist_reset', params: {token: result.token}});
      p.offset = 0; p.previous = [];
      draw(current, pending.page, false);
      say({tasks: 'Выполнение сброшено.', order: 'Ручной порядок сброшен.'}[pending.what] || 'Прогресс плейлиста сброшен.');
    }
  } catch (error) { pendingAction = null; say(error.message); }
}

// --- the order editor: each move saved at once ------------------------------
async function editorMove(page, entry, position) {
  const p = page.params;
  try {
    await api('/api/command', {name: 'playlist_move', params: {entity: p.entity, token: p.token, entry_id: entry, position}});
    await api('/api/command', {name: 'playlist_save', params: {entity: p.entity, token: p.token}});
    p.changed = true;
    return true;
  } catch (error) { say(error.message); return false; }
  finally { delete p.token; }
}
function editorTap(page, item) {
  const p = page.params;
  if (p.pick == null) p.pick = item.entry_id;
  else if (p.pick === item.entry_id) p.pick = null;
  else {
    const items = page.items || [];
    const old = (items.find(v => v.entry_id === p.pick) || {}).position;
    if (old === undefined) { p.pick = null; }
    else { editorPlace(page, old < item.position ? item.position - 1 : item.position); return; }
  }
  draw(current, page, true);
}
async function editorPlace(page, position) {
  const p = page.params, ident = p.pick;
  p.pick = null;
  const old = ((page.items || []).find(v => v.entry_id === ident) || {}).position;
  if (await editorMove(page, ident, position)) p.undo = (p.undo || []).concat([[ident, old]]);
  draw(current, page, true);
}
async function editorStep(page, item, step) {
  const p = page.params;
  p.pick = null;
  if (await editorMove(page, item.entry_id, item.position + step)) p.undo = (p.undo || []).concat([[item.entry_id, item.position]]);
  draw(current, page, true);
}
async function editorUndo(page) {
  const p = page.params, [ident, old] = p.undo.pop();
  await editorMove(page, ident, old);
  draw(current, page, true);
}
async function editorReset(page) {
  const p = page.params;
  try {
    const result = await query('reset_preview', {entity: p.entity, order: p.order, what: 'order'});
    if (result.loading || result.failed) { say('Не удалось проверить плейлист.'); return; }
    const custom = p.order === 'custom';
    if (!await ask(custom ? 'Вернуть порядок сервиса? Ваши перестановки в этом плейлисте пропадут.' :
                   'Сбросить поправки внутри уроков этого плейлиста?', custom ? 'Вернуть порядок сервиса' : 'Сбросить поправки')) return;
    if (p.token) { await api('/api/command', {name: 'playlist_cancel', params: {entity: p.entity, token: p.token}}).catch(() => {}); delete p.token; }
    await api('/api/command', {name: 'playlist_reset', params: {token: result.token}});
    p.undo = []; p.pick = null; p.changed = true;
    draw(current, page, true);
  } catch (error) { say(error.message); }
}

// --- bookmarks by link ------------------------------------------------------
async function addLink() {
  await modal('Добавить закладку', (box, finish) => {
    const input = el('input'); input.type = 'url'; input.placeholder = 'Ссылка на видео, плейлист или канал';
    const status = el('p');
    const add = button('Добавить', async () => {
      add.disabled = true; status.textContent = 'Проверяю ссылку…';
      try { await api('/api/bookmark-link', {url: input.value}); finish(true); scheduleRefresh(); }
      catch (error) { status.textContent = error.message; add.disabled = false; }
    }, 'primary');
    box.append(input, add, status);
  });
}

// --- keeping up with the PC -------------------------------------------------
function scheduleRefresh() {
  if (document.hidden) return;
  if (refreshBusy) { refreshAgain = true; return; }
  if (refreshTimer) return;
  refreshTimer = setTimeout(async () => {
    refreshTimer = null; refreshBusy = true;
    try {
      state = await query('state');
      updateScope();
      // The page on show drawn again where it was; the kept ones when shown.
      Object.values(panes).concat(prev ? [prev.pane] : []).forEach(pane => { if (pane !== current) pane.stale = true; });
      if (current && current.page && !(current.page.view === 'playlist_editor' && current.page.params.pick != null)) {
        const page = current.page;
        if (page.view === 'playlist_editor') page.items = null;
        await draw(current, page, true);
      }
      if (pendingAction) await attemptAction();
      if (pendingPlay) await attemptPlay();
    } catch (error) { say(error.message); }
    finally {
      refreshBusy = false;
      if (refreshAgain) { refreshAgain = false; scheduleRefresh(); }
    }
  }, 200);
}

// --- playing ----------------------------------------------------------------
async function play(item, resume = false, startOver = false) {
  const page = sub && sub.view === 'playlist' ? sub : null;
  pendingPlay = {video: item, playlist: page ? page.params.entity : null, order: page ? page.params.order : null,
                 resume, start_over: startOver,
                 request: crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random(),
                 previous: playing && playing.id};
  await attemptPlay();
}
async function attemptPlay() {
  const pending = pendingPlay;
  if (!pending || pending.busy) return;
  pending.busy = true;
  const generation = ++playGeneration;
  try {
    say('Подготавливаю видео…');
    const result = await api('/api/play', Object.assign({}, pending, {quality: Number($('quality').value)}));
    if (generation !== playGeneration || pendingPlay !== pending) { if (result.id) api('/api/stop', {id: result.id}).catch(() => {}); return; }
    if (result.loading) return;
    pendingPlay = null;
    await installPlayback(result);
    say('');
  } catch (error) { if (generation === playGeneration) { pendingPlay = null; say(error.message); } }
  finally { pending.busy = false; }
}
async function installPlayback(session) {
  session.ready = false; playing = session; reportedAt = 0;
  $('player').hidden = false; $('playing-title').textContent = session.video.title;
  $('service-link').href = session.service_url;
  $('previous').disabled = !session.previous; $('next').disabled = !session.next;
  $('player-note').textContent = ''; $('retry').hidden = true;
  clearTimeout(prepareTimer);
  prepareTimer = setTimeout(() => {
    if (playing === session && video.readyState < HTMLMediaElement.HAVE_METADATA) mediaFailed(session);
  }, PREPARE_MS);
  video.controls = session.mode === 'direct'; video.src = session.url;
  video.onloadedmetadata = () => {
    if (playing !== session) return;
    clearTimeout(prepareTimer);
    if (session.mode === 'direct' && session.pos > 0) video.currentTime = session.pos;
    session.ready = true; updateTimeline();
  };
  // Not awaited: the promise of a video that never loads never settles. A
  // failed load rejects it too, after the error handler told why; only an
  // autoplay the browser refused asks for a tap.
  video.play().catch(error => {
    if (playing === session && !session.failed && error.name === 'NotAllowedError')
      $('player-note').textContent = 'Нажмите ▶, чтобы начать просмотр.';
  });
  window.scrollTo({top: 0, behavior: 'smooth'});
}
// Told once per session, and only while it is the one on show: a late error
// of a replaced or seeked stream changes nothing. The browser stops fetching;
// «Повторить» asks the PC for the stream again, nothing retries by itself.
function mediaFailed(session) {
  if (playing !== session || session.failed) return;
  session.failed = true; session.retryAt = session.ready ? position() : session.pos; session.ready = false;
  clearTimeout(prepareTimer);
  video.removeAttribute('src'); video.load();
  $('player-note').textContent = MEDIA_FAILED; $('retry').hidden = false;
  updateTimeline();
}
function retry() {
  if (playing && playing.failed) seek(playing.retryAt);
}
function position() { return playing ? (playing.offset || 0) + (video.currentTime || 0) : 0; }
function duration() { return playing && (playing.duration || (Number.isFinite(video.duration) ? video.duration : null)); }
function updateTimeline() {
  if (!playing) return;
  const d = duration(), pos = position();
  $('time').textContent = time(pos) + (d ? ' / ' + time(d) : '');
  if (!seeking) { $('timeline').max = d || Math.max(1, pos); $('timeline').value = pos; }
  $('timeline').disabled = !d;
  $('toggle-play').textContent = video.paused ? '▶' : '⏸';
}
async function report(force = false, beacon = false, ended = false) {
  if (!playing || !playing.ready) return;
  const now = Date.now();
  if (!force && now - reportedAt < 5000) return;
  reportedAt = now;
  const data = {id: playing.id, generation: playing.generation, pos: position(), duration: duration(), paused: video.paused, ended, force};
  if (beacon && navigator.sendBeacon) { navigator.sendBeacon('/api/progress', new Blob([JSON.stringify(data)], {type: 'application/json'})); return; }
  try {
    const result = await api('/api/progress', data);
    if (!result.saved) $('player-note').textContent = 'Общий прогресс сохраняется на другом устройстве.';
  } catch { /* The last progress the PC took stays. */ }
}
async function seek(pos, quality = null) {
  if (!playing) return;
  await report(true);
  const session = playing;
  try { const result = await api('/api/seek', {id: session.id, pos, quality}); if (playing === session) await installPlayback(result); }
  catch (error) { if (playing === session) $('player-note').textContent = error.message; }
}
function clearPlayer() {
  playGeneration++; pendingPlay = null; playing = null; clearTimeout(prepareTimer);
  video.pause(); video.removeAttribute('src'); video.load(); $('player').hidden = true;
}
async function stopPlayer() {
  if (!playing) return;
  const id = playing.id;
  await report(true); clearPlayer();
  try { await api('/api/stop', {id}); } catch { /* gone already */ }
}
async function adjacent(which) {
  if (!playing || !playing[which]) return;
  await report(true);
  pendingPlay = {adjacent: which, previous: playing.id, request: String(Date.now()) + Math.random()};
  await attemptPlay();
}
async function refreshNeighbors() {
  const session = playing;
  if (!session) return;
  const generation = ++neighborsGeneration;
  try {
    const result = await api('/api/playback', {id: session.id});
    if (playing === session && generation === neighborsGeneration) {
      session.previous = result.previous; session.next = result.next;
      $('previous').disabled = !session.previous; $('next').disabled = !session.next;
    }
  } catch { /* the next event tells */ }
}
// The whole screen and back: ⛶ beside the title, or a double tap on the picture.
async function playerFullscreen() {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else if ($('player').requestFullscreen) await $('player').requestFullscreen();
    else if (video.webkitEnterFullscreen) video.webkitEnterFullscreen();
  } catch { $('player-note').textContent = 'Полноэкранный режим недоступен в этом браузере.'; }
}
let lastTap = 0;
video.addEventListener('pointerup', event => {
  if (event.pointerType !== 'touch') return;
  const now = Date.now();
  if (now - lastTap < 350) { lastTap = 0; playerFullscreen(); } else lastTap = now;
});
video.ondblclick = playerFullscreen;
video.ontimeupdate = () => { updateTimeline(); report(); };
video.onpause = () => { updateTimeline(); report(true); };
video.onseeked = () => report(true);
video.onended = async () => { await report(true, false, true); if (playing && playing.next) await adjacent('next'); };
// An error of the stream before the one on show (its src since replaced) is not this session's.
video.onerror = () => {
  if (playing && video.error && video.currentSrc === new URL(playing.url, location.href).href) mediaFailed(playing);
};
$('toggle-play').onclick = () => {
  if (playing && playing.failed) retry();
  else if (video.paused) {
    const session = playing;
    video.play().catch(() => { if (playing === session && session && !session.failed) $('player-note').textContent = 'Не удалось начать просмотр.'; });
  } else video.pause();
};
$('retry').onclick = retry;
$('previous').onclick = () => adjacent('previous');
$('next').onclick = () => adjacent('next');
$('close-player').onclick = stopPlayer;
$('player-full').onclick = playerFullscreen;
$('timeline').oninput = () => { seeking = true; $('time').textContent = time(Number($('timeline').value)); };
$('timeline').onchange = () => {
  seeking = false;
  const pos = Number($('timeline').value);
  if (playing && playing.mode === 'direct') video.currentTime = pos; else seek(pos);
};
$('quality').onchange = () => seek(position(), Number($('quality').value));

// --- the row on top ---------------------------------------------------------
$('back').onclick = () => { if (history.state && history.state.sub && sub) history.back(); else back(); };
window.addEventListener('popstate', () => { if (sub || mode === 'search') back(); });
document.querySelectorAll('#tabs button').forEach(b => { b.onclick = () => tabClicked(b.dataset.page); });
$('find').onclick = openSearch;
$('settings').onclick = () => openSub('settings', {}, 'Настройки', sub && sub.view === 'settings');
$('scope').onclick = () => {
  const choices = ['all'].concat(state.sources.filter(s => s.enabled).map(s => s.id));
  const now = choices.includes(state.scope) ? state.scope : 'all';
  command('scope', {source: choices[(choices.indexOf(now) + 1) % choices.length]})
    .then(() => { state.scope = choices[(choices.indexOf(now) + 1) % choices.length]; updateScope(); if (sub && sub.view === 'search') draw(current, sub, false); });
};
$('search-form').onsubmit = event => {
  event.preventDefault();
  const text = $('query').value.trim();
  $('query').blur();  // the keyboard goes away
  if (!sub && tab === 'bookmarks') { bookmarkQuery = text; showTab('bookmarks', true); return; }
  if (text) openSub('search', {query: text, kind: 'search'}, 'Поиск', sub && sub.view === 'search');
};
$('fullscreen').onclick = async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else if (document.documentElement.requestFullscreen) await document.documentElement.requestFullscreen();
    else say('Полноэкранный режим недоступен в этом браузере.');
  } catch { say('Для полноэкранного режима нажмите кнопку ещё раз.'); }
};
window.addEventListener('resize', () => {
  // The rows of Главная hold as many cards as the window has columns.
  if (panes.home && current !== panes.home) panes.home.stale = true;
  else if (current === panes.home && current.columns !== columns()) { current.columns = columns(); draw(current, current.page, true); }
});

// --- pairing and connecting -------------------------------------------------
function needPair() {
  if (events) { events.close(); events = null; }
  clearPlayer();
  $('pair').hidden = false; $('panes').hidden = true; $('bar').hidden = true;
}
$('pair-form').onsubmit = async event => {
  event.preventDefault();
  try {
    await api('/pair', {code: $('pair-code').value.trim(), name: $('device-name').value});
    $('pair-code').value = '';
    await connect();
  } catch (error) { $('pair-note').textContent = error.message; }
};
async function connect() {
  try {
    state = await query('state');
    $('pair').hidden = true; $('panes').hidden = false; $('bar').hidden = false;
    if (events) events.close();
    events = new EventSource('/events');
    events.onmessage = event => {
      const data = JSON.parse(event.data);
      if (data.type === 'shutdown') { events.close(); events = null; clearPlayer(); say('Видео на ПК отключено. Переподключитесь после запуска приложения.'); return; }
      if (data.type === 'device_revoked' && data.data.device === state.device) { needPair(); return; }
      if (data.type === 'playback_stopped' && playing && playing.id === data.data.id) {
        if (data.data.reason === 'replaced' && pendingPlay && pendingPlay.previous === playing.id) return;
        clearPlayer(); return;
      }
      if (data.type === 'queue_changed') { if (playing && playing.id === data.data.id) refreshNeighbors(); return; }
      if (data.type === 'resync') refreshNeighbors();
      if (data.type === 'library' && data.data.what === 'position') return;
      if (data.type === 'playback') return;
      if (data.type === 'resync' || (data.revision || 0) > revision) { revision = Math.max(revision, data.revision || 0); scheduleRefresh(); }
    };
    events.onerror = () => say('Связь с ПК прервана. Восстанавливаю соединение…');
    updateScope();
    if (!current) showTab('home');
    else if (current.page) draw(current, current.page, true);
  } catch (error) {
    if (!$('pair').hidden) $('pair-note').textContent = error.message;
    else { if (!current) showTab('home'); say('ПК недоступен. Проверьте, что приложение открыто и доступ из браузера включён.'); }
  }
}
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { report(true, true); if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; } }
  else connect();
});
window.addEventListener('pagehide', () => report(true, true));
const fragment = new URLSearchParams(location.hash.slice(1));
if (fragment.has('pair')) {
  $('pair-code').value = fragment.get('pair');
  window.history.replaceState(null, '', location.pathname);
  needPair();
  $('pair-form').requestSubmit();
} else connect();
