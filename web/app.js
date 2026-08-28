'use strict';

const $ = (id) => document.getElementById(id);
const POSITIONS = ['QB', 'RB', 'WR', 'TE', 'K', 'DEF'];

// Primary colour and a nickname per NFL team, used to brand your team's
// players on the board.
const TEAMS = {
  ARI: ['#97233F', 'Cardinals', '🐦'], ATL: ['#A71930', 'Falcons', '🦅'],
  BAL: ['#241773', 'Ravens', '🐦‍⬛'],  BUF: ['#00338D', 'Bills', '🦬'],
  CAR: ['#0085CA', 'Panthers', '🐆'], CHI: ['#0B162A', 'Bears', '🐻'],
  CIN: ['#FB4F14', 'Bengals', '🐅'],  CLE: ['#FF3C00', 'Browns', '🐕'],
  DAL: ['#041E42', 'Cowboys', '⭐'],  DEN: ['#FB4F14', 'Broncos', '🐴'],
  DET: ['#0076B6', 'Lions', '🦁'],    GB:  ['#FFB612', 'Packers', '🧀'],
  HOU: ['#03202F', 'Texans', '🐂'],   IND: ['#002C5F', 'Colts', '🐎'],
  JAX: ['#00839C', 'Jaguars', '🐆'],  KC:  ['#E31837', 'Chiefs', '🏹'],
  LAC: ['#0080C6', 'Chargers', '⚡'], LAR: ['#003594', 'Rams', '🐏'],
  LV:  ['#A5ACAF', 'Raiders', '🏴‍☠️'], MIA: ['#008E97', 'Dolphins', '🐬'],
  MIN: ['#4F2683', 'Vikings', '🛡️'],  NE:  ['#0C2340', 'Patriots', '🇺🇸'],
  NO:  ['#D3BC8D', 'Saints', '⚜️'],   NYG: ['#0B2265', 'Giants', '🗽'],
  NYJ: ['#125740', 'Jets', '✈️'],     PHI: ['#00814A', 'Eagles', '🦅'],
  PIT: ['#FFB612', 'Steelers', '🔩'], SEA: ['#69BE28', 'Seahawks', '🦅'],
  SF:  ['#AA0000', '49ers', '⛏️'],    TB:  ['#D50A0A', 'Buccaneers', '🏴‍☠️'],
  TEN: ['#4B92DB', 'Titans', '⚔️'],   WAS: ['#5A1414', 'Commanders', '🪶'],
};

/** Turn #rrggbb into an rgba() string, for the row tint. */
function tint(hex, alpha) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || '');
  if (!m) return `rgba(88,166,255,${alpha})`;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
}

const state = {
  board: [],          // full ranked player list, fetched once
  drafted: new Set(),
  manual: new Set(),  // ids you marked by hand, which you can undo
  live: null,
  status: null,
  filter: 'ALL',
  query: '',
  showDrafted: false,
  sortKey: 'vorp',
  sortDir: -1,       // -1 = descending
  favTeam: '',
  favOnly: false,
  timer: null,
  tick: null,
  // Offset between this browser's clock and the server's, so the countdown
  // stays right even if the laptop clock is off.
  clockSkew: 0,
  lastMark: null,
  undoTimer: null,
};

const pad = (n) => String(n).padStart(2, '0');

function humanCountdown(ms) {
  if (ms < 0) ms = 0;
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}h ${pad(m)}m`;
  if (m > 0) return `${m}:${pad(s)}`;
  return `0:${pad(s)}`;
}

/* ---------------- helpers ---------------- */

async function api(path, opts) {
  const res = await fetch(path, opts);
  let data;
  try {
    data = await res.json();
  } catch {
    throw new Error(`Server returned ${res.status}`);
  }
  if (data && data.error) throw new Error(data.error);
  if (!res.ok) throw new Error(`Server returned ${res.status}`);
  return data;
}

const post = (path, body) =>
  api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });

const esc = (s) =>
  String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const posTag = (p, rank) =>
  `<span class="pos pos-${esc(p)}">${esc(p)}</span>${rank ? `<span class="posr">${rank}</span>` : ''}`;

/* ---------------- setup screen ---------------- */

async function findLeagues() {
  const username = $('username').value.trim();
  if (!username) return;
  $('setup-error').textContent = '';
  $('setup-status').textContent = 'looking up your leagues…';
  $('find-leagues').disabled = true;
  try {
    const data = await api(`/api/leagues?username=${encodeURIComponent(username)}`);
    renderLeagues(data);
    $('setup-status').textContent = data.leagues.length
      ? `${data.leagues.length} league(s) for ${data.season}`
      : `No ${data.season} leagues found for that username.`;
  } catch (err) {
    $('setup-error').textContent = err.message;
    $('setup-status').textContent = '';
  } finally {
    $('find-leagues').disabled = false;
  }
}

function renderLeagues(data) {
  const list = $('league-list');
  list.innerHTML = '';
  for (const lg of data.leagues) {
    const el = document.createElement('div');
    el.className = 'league-item';
    const live = lg.draft_status === 'drafting';
    el.innerHTML = `
      <div>
        <div class="li-name">${esc(lg.name)}</div>
        <div class="li-meta">${esc(lg.teams)} teams · ${esc(lg.draft_type || 'draft')} · ${esc(lg.draft_status || 'no draft')}</div>
      </div>
      <span class="badge ${live ? 'live' : ''}">${live ? 'DRAFTING' : esc(lg.status || '')}</span>`;
    el.onclick = () => connect({ league_id: lg.league_id, draft_id: lg.draft_id, username: data.username });
    list.appendChild(el);
  }
}

async function connect(payload) {
  $('setup-error').textContent = '';
  $('setup-status').textContent = 'loading players and projections (first run takes a few seconds)…';
  try {
    state.status = await post('/api/connect', payload);
    const board = await api('/api/board');
    state.board = board.players || [];
    $('setup').classList.add('hidden');
    $('board').classList.remove('hidden');
    renderHeader();
    await tick();
    startPolling();
  } catch (err) {
    $('setup-error').textContent = err.message;
    $('setup-status').textContent = '';
  }
}

/* ---------------- polling ---------------- */

function startPolling() {
  if (state.timer) clearInterval(state.timer);
  state.timer = setInterval(tick, 2500);
  // The countdown redraws every second locally rather than waiting on the
  // poll, so it counts down smoothly instead of jumping in 2.5s steps.
  if (state.tick) clearInterval(state.tick);
  state.tick = setInterval(renderCountdown, 1000);
}

/** Time until the draft starts, or time left on the current pick. */
function renderCountdown() {
  const el = $('countdown');
  const l = state.live;
  if (!l) return el.classList.add('hidden');

  const now = Date.now() + state.clockSkew;

  // Before the draft: count down to the scheduled start.
  if (l.status && l.status !== 'drafting' && l.status !== 'complete') {
    if (l.start_time && l.start_time > now) {
      el.textContent = `Draft starts in ${humanCountdown(l.start_time - now)}`;
      el.className = 'countdown pre';
    } else {
      el.textContent = l.start_time ? 'Draft starting…' : 'Draft not scheduled yet';
      el.className = 'countdown pre';
    }
    return;
  }

  if (l.status === 'complete') {
    el.textContent = 'Draft complete';
    el.className = 'countdown';
    return;
  }

  // During the draft: time left on the pick clock, if the league uses one.
  if (l.pick_timer && l.last_picked) {
    const deadline = l.last_picked + l.pick_timer * 1000;
    const left = deadline - now;
    if (left > 0) {
      el.textContent = `${humanCountdown(left)} on this pick`;
      el.className = 'countdown' + (left < 30000 ? ' urgent' : '');
    } else {
      el.textContent = 'pick clock expired';
      el.className = 'countdown urgent';
    }
    return;
  }
  el.classList.add('hidden');
}

async function tick() {
  try {
    const live = await api('/api/live');
    if (!live.connected) return;
    state.live = live;
    state.drafted = new Set(live.drafted || []);
    state.manual = new Set(live.manual || []);
    if (live.server_now) state.clockSkew = live.server_now - Date.now();
    setSync(true, live.error);
    renderClock();
    renderCountdown();
    renderRecs();
    renderRoster();
    renderRecent();
    renderTable();
  } catch (err) {
    setSync(false, err.message);
  }
}

function setSync(ok, error) {
  const dot = $('sync-dot');
  dot.className = 'dot ' + (error ? 'err' : ok ? 'on' : '');
  $('sync-text').textContent = error ? error.slice(0, 60) : ok ? 'live' : 'reconnecting…';
}

/* ---------------- rendering ---------------- */

function renderHeader() {
  const s = state.status;
  if (!s || !s.connected) return;
  $('league-name').textContent = s.league.name;
  $('league-scoring').textContent = s.league.scoring_label;
  $('league-size').textContent = `${s.league.teams} teams · ${s.draft.rounds} rd · ${s.draft.type}`;

  applyFavoriteTeam(s.favorite_team);

  const slot = s.draft.my_slot;
  $('my-team').textContent = s.draft.is_auction
    ? (s.draft.budget ? `Auction · $${s.draft.budget}` : 'Auction')
    : slot ? `You: pick ${slot}` : 'Slot unknown';

  const notes = [];
  if (s.draft.is_auction) {
    notes.push(
      'AUCTION DRAFT detected — player values, tiers and roster needs are live, ' +
      'but pick-timing advice is hidden because it does not apply. There is no ' +
      '$-value or max-bid guidance.'
    );
  }
  if (s.board_source === 'adp') {
    notes.push('Projections unavailable — values are ADP-derived estimates.');
  }
  (s.notes || []).forEach((n) => notes.push(n));
  if (!slot && !s.draft.is_auction) {
    notes.push('Could not match your username to a draft slot — "Your roster" and pick timing are off until it starts.');
  }
  const box = $('board-notes');
  box.innerHTML = notes.map(esc).join(' · ');
  box.classList.toggle('hidden', notes.length === 0);
}

/** Brand the board with your team's real colours and reveal its filter. */
function applyFavoriteTeam(code) {
  const team = (code || '').toUpperCase();
  state.favTeam = TEAMS[team] ? team : '';
  const label = $('fav-chk-label');
  if (!state.favTeam) {
    label.classList.add('hidden');
    return;
  }
  const [color, nickname, emoji] = TEAMS[state.favTeam];
  const root = document.documentElement;
  root.style.setProperty('--fav', color);
  root.style.setProperty('--fav-tint', tint(color, 0.13));
  $('fav-chk-text').textContent = `${emoji} ${nickname} only`;
  label.classList.remove('hidden');
  label.title = `Show only available ${nickname}`;
}

function renderClock() {
  const l = state.live;
  const teams = state.status.draft.teams;

  // An auction has no pick order, so showing a round/pick clock or a
  // "picks until your turn" countdown would be inventing information.
  if (l.is_auction) {
    const spent = l.my_spend || 0;
    const budget = l.budget || 0;
    $('clock-main').textContent = `Auction · ${l.picks_made} players rostered`;
    $('clock-sub').textContent = budget
      ? `you've spent $${spent} of $${budget} · $${budget - spent} left`
      : `$${spent} spent`;
    $('turn-banner').classList.add('hidden');
    document.title = 'Auction — Draft Board';
    return;
  }

  $('clock-main').textContent = `Round ${l.round} · Pick ${l.on_the_clock} overall`;

  const until = l.picks_until_turn;
  let sub;
  if (l.is_my_turn) sub = 'your pick';
  else if (until == null) sub = `${l.picks_made} of ${teams * state.status.draft.rounds} picks made`;
  else if (until === 1) sub = 'you are next';
  else sub = `${until} picks until your turn · next up ${(l.next_picks || []).slice(0, 3).join(', ')}`;
  $('clock-sub').textContent = sub;

  $('turn-banner').classList.toggle('hidden', !l.is_my_turn);
  document.title = l.is_my_turn ? '🟢 YOUR PICK — Draft Board' : `Pick ${l.on_the_clock} — Draft Board`;
}

function renderRecs() {
  const box = $('recs');
  const recs = (state.live && state.live.recommendations) || [];
  if (!recs.length) {
    box.innerHTML = '<div class="empty">No suggestions yet.</div>';
    return;
  }
  const auction = state.live.is_auction;
  box.innerHTML = recs
    .map((r, i) => {
      const val = !auction && r.adp != null ? Math.round(r.adp - state.live.on_the_clock) : null;
      const survival = Math.round((r.survival || 0) * 100);
      const isFav = state.favTeam && r.team === state.favTeam;
      return `
      <div class="rec ${i === 0 ? 'top' : ''} ${isFav ? 'fav-rec' : ''}">
        <div class="rec-head">
          <span class="rec-name">${esc(r.name)}${isFav ? ` <span class="fav-mark">${TEAMS[state.favTeam][2]}</span>` : ''}</span>
          <span class="rec-score">${r.vorp > 0 ? '+' : ''}${r.vorp} VORP</span>
        </div>
        <div class="rec-meta">
          ${posTag(r.pos, r.pos_rank)}
          <span class="muted">${esc(r.team || 'FA')}</span>
          <span class="tier-chip">T${r.tier}</span>
          ${r.adp != null && !auction ? `<span class="muted">ADP ${r.adp}${val != null ? ` (${val > 0 ? '+' : ''}${val})` : ''}</span>` : ''}
          ${auction ? '' : `<span class="muted">${survival}% to last</span>`}
        </div>
        ${r.reason ? `<div class="rec-why">${esc(r.reason)}</div>` : ''}
      </div>`;
    })
    .join('');
}

function renderRoster() {
  const l = state.live;
  const counts = l.roster_counts || {};
  const starters = state.status.league.starters || {};
  const flex = state.status.league.flex_slots || {};

  const needs = POSITIONS.map((pos) => {
    const need = starters[pos] || 0;
    const have = counts[pos] || 0;
    const cls = need && have < need ? 'open' : have >= need && need ? 'full' : '';
    return `<span class="need ${cls}">${pos} ${have}${need ? `/${need}` : ''}</span>`;
  });
  for (const [slot, n] of Object.entries(flex)) {
    needs.push(`<span class="need">${esc(slot.replace('_', ' '))} ×${n}</span>`);
  }
  $('needs').innerHTML = needs.join('');

  const roster = l.roster || [];
  $('roster-count').textContent = `${roster.length}/${state.status.league.roster_size}`;
  $('roster').innerHTML = roster.length
    ? roster
        .map((p) => `<li>${posTag(p.pos)}<span class="r-name">${esc(p.name)}</span><span class="muted">${p.pts}</span></li>`)
        .join('')
    : '<li class="empty">No picks yet.</li>';
}

function renderRecent() {
  const l = state.live;
  const runs = Object.entries(l.runs || {})
    .sort((a, b) => b[1] - a[1])
    .filter(([, n]) => n >= 3)
    .map(([pos, n]) => `${pos} run (${n}/10)`)
    .join(' · ');
  $('runs').textContent = runs;

  const recent = l.recent || [];
  $('recent').innerHTML = recent.length
    ? recent
        .map(
          (p) => `<li>
            <span class="r-pick">${l.is_auction && p.amount ? '$' + p.amount : esc(p.pick_no)}</span>
            ${posTag(p.pos)}
            <span class="r-name">${esc(p.name)}</span>
            <span class="r-team">${esc(p.team)}</span>
          </li>`
        )
        .join('')
    : '<li class="empty">Waiting for the first pick…</li>';
}

// The direction each column is most useful in on first click: best players
// first for value columns, earliest picks first for ADP, biggest bargains
// first for Val.
const SORT_DIR = { tier: 1, name: 1, pts: -1, vorp: -1, adp: 1, val: -1 };

function sortValue(p, key, onClock) {
  switch (key) {
    case 'tier': return p.tier;
    case 'name': return p.name.toLowerCase();
    case 'pts': return p.pts;
    case 'adp': return p.adp;
    case 'val': return p.adp == null ? null : p.adp - onClock;
    default: return p.vorp;
  }
}

/** Sort the board, always keeping players with no value for that column last. */
function sortedBoard(onClock) {
  const { sortKey, sortDir } = state;
  return state.board.slice().sort((a, b) => {
    const av = sortValue(a, sortKey, onClock);
    const bv = sortValue(b, sortKey, onClock);
    const aNull = av === null || av === undefined;
    const bNull = bv === null || bv === undefined;
    if (aNull || bNull) return aNull && bNull ? 0 : aNull ? 1 : -1;
    if (av < bv) return -sortDir;
    if (av > bv) return sortDir;
    return b.vorp - a.vorp;   // stable, meaningful tiebreak
  });
}

function renderSortHeaders() {
  document.querySelectorAll('th.sortable').forEach((th) => {
    const active = th.dataset.sort === state.sortKey;
    th.classList.toggle('active', active);
    let arrow = th.querySelector('.arrow');
    if (!arrow) {
      arrow = document.createElement('span');
      arrow.className = 'arrow';
      th.appendChild(arrow);
    }
    const dir = active ? state.sortDir : SORT_DIR[th.dataset.sort];
    arrow.textContent = dir === 1 ? '▲' : '▼';
  });
}

/** The × / ↺ cell. Only marks you made by hand can be undone here. */
function actionCell(p, isDrafted) {
  const manual = state.manual && state.manual.has(p.id);
  if (isDrafted && !manual) {
    return '<span class="muted" title="Drafted in Sleeper — can\'t be undone here">·</span>';
  }
  return `<button class="mark" data-id="${esc(p.id)}" data-drafted="${manual ? '1' : '0'}"
    title="${manual ? 'Undo this manual mark' : 'Mark as drafted'}">${manual ? '↺' : '×'}</button>`;
}

function renderTable() {
  const body = $('players-body');
  // "value vs. current pick" is meaningless without a pick order.
  const auction = state.live && state.live.is_auction;
  const onClock = state.live && !auction ? state.live.on_the_clock : 0;
  const query = state.query;

  const rows = [];
  let shown = 0;
  let lastTier = null;
  // Tier dividers only make sense while the board is ordered by value.
  const valueOrdered = state.sortKey === 'vorp' || state.sortKey === 'pts';

  renderSortHeaders();
  for (const p of sortedBoard(state.live ? state.live.on_the_clock : 0)) {
    if (shown >= 200) break;
    const isDrafted = state.drafted.has(p.id);
    if (isDrafted && !state.showDrafted) continue;
    if (state.filter !== 'ALL' && p.pos !== state.filter) continue;
    if (state.favOnly && p.team !== state.favTeam) continue;
    if (query && !p.name.toLowerCase().includes(query)) continue;
    shown++;

    const val = p.adp != null && onClock ? Math.round(p.adp - onClock) : null;
    const valCls = val == null ? '' : val >= 10 ? 'val-good' : val <= -10 ? 'val-bad' : '';
    // Only mark tier breaks when looking at a single position; across
    // positions the tier numbers interleave and the rule is just noise.
    const isBreak =
      valueOrdered && state.filter !== 'ALL' && lastTier !== null && p.tier !== lastTier;
    lastTier = p.tier;

    const isFav = state.favTeam && p.team === state.favTeam;
    rows.push(`<tr class="${isDrafted ? 'drafted' : ''} ${isBreak ? 'tierbreak' : ''} ${isFav ? 'fav' : ''}">
      <td class="c-tier"><span class="tier-chip">${p.tier}</span></td>
      <td class="c-name">
        <span class="p-name">${esc(p.name)}</span>
        <span class="p-team">${esc(p.team || 'FA')}</span>
        ${isFav ? `<span class="fav-mark">${TEAMS[state.favTeam][2]}</span>` : ''}
        ${p.injury ? `<span class="p-inj">${esc(p.injury)}</span>` : ''}
      </td>
      <td class="c-pos">${posTag(p.pos, p.pos_rank)}</td>
      <td class="c-num">${p.pts}</td>
      <td class="c-num">${p.vorp > 0 ? '+' : ''}${p.vorp}</td>
      <td class="c-num">${p.adp != null ? p.adp : '—'}</td>
      <td class="c-num ${valCls}">${val == null ? '—' : (val > 0 ? '+' : '') + val}</td>
      <td class="c-act">${actionCell(p, isDrafted)}</td>
    </tr>`);
  }

  body.innerHTML = rows.length ? rows.join('') : '<tr><td colspan="8" class="empty">No players match.</td></tr>';
}

/* ---------------- events ---------------- */

$('find-leagues').onclick = findLeagues;
$('username').addEventListener('keydown', (e) => { if (e.key === 'Enter') findLeagues(); });

$('connect-id').onclick = () => {
  const draftId = $('draft-id').value.trim();
  if (draftId) connect({ draft_id: draftId, username: $('username').value.trim() });
};

$('posfilter').onclick = (e) => {
  const btn = e.target.closest('button');
  if (!btn) return;
  state.filter = btn.dataset.pos;
  [...$('posfilter').children].forEach((b) => b.classList.toggle('active', b === btn));
  renderTable();
};

$('search').oninput = (e) => {
  state.query = e.target.value.trim().toLowerCase();
  renderTable();
};

$('show-drafted').onchange = (e) => {
  state.showDrafted = e.target.checked;
  renderTable();
};

// Click a column to sort; click the same one again to flip direction.
document.querySelector('#players thead').onclick = (e) => {
  const th = e.target.closest('th.sortable');
  if (!th) return;
  const key = th.dataset.sort;
  state.sortDir = state.sortKey === key ? -state.sortDir : (SORT_DIR[key] || -1);
  state.sortKey = key;
  renderTable();
};

$('fav-only').onchange = (e) => {
  state.favOnly = e.target.checked;
  renderTable();
};

// ---- theme ----
// Start from the saved choice, else follow the OS. Storage can throw in a
// private window, so every access is guarded.
function readStoredTheme() {
  try {
    return localStorage.getItem('draftkit-theme');
  } catch {
    return null;
  }
}

function applyTheme(mode) {
  document.documentElement.setAttribute('data-theme', mode);
  $('theme-btn').textContent = mode === 'light' ? '☀️' : '🌙';
  $('theme-btn').title = mode === 'light' ? 'Switch to dark' : 'Switch to light';
  try {
    localStorage.setItem('draftkit-theme', mode);
  } catch {
    /* not persisting is fine; the toggle still works for this session */
  }
}

const prefersLight =
  window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
applyTheme(readStoredTheme() || (prefersLight ? 'light' : 'dark'));

$('theme-btn').onclick = () => {
  const now = document.documentElement.getAttribute('data-theme');
  applyTheme(now === 'light' ? 'dark' : 'light');
};

// Manual mark-off, so the board stays correct even if Sleeper's API lags.
$('players-body').onclick = async (e) => {
  const btn = e.target.closest('button.mark');
  if (!btn) return;
  const drafted = btn.dataset.drafted === '1';
  const id = btn.dataset.id;
  btn.disabled = true;
  try {
    await post('/api/mark', { player_id: id, drafted: !drafted });
    // Marking hides the player, so offer the way back immediately rather than
    // making you hunt for him behind "show drafted".
    if (!drafted) showUndo(id);
    else hideUndo();
    await tick();
  } catch (err) {
    setSync(false, err.message);
  }
};

function showUndo(id) {
  const player = state.board.find((p) => p.id === id);
  state.lastMark = id;
  $('undo-text').textContent = `Marked ${player ? player.name : 'player'} as drafted.`;
  $('undo-bar').classList.remove('hidden');
  if (state.undoTimer) clearTimeout(state.undoTimer);
  state.undoTimer = setTimeout(hideUndo, 12000);
}

function hideUndo() {
  state.lastMark = null;
  $('undo-bar').classList.add('hidden');
  if (state.undoTimer) clearTimeout(state.undoTimer);
}

$('undo-btn').onclick = async () => {
  if (!state.lastMark) return hideUndo();
  const id = state.lastMark;
  hideUndo();
  try {
    await post('/api/mark', { player_id: id, drafted: false });
    await tick();
  } catch (err) {
    setSync(false, err.message);
  }
};

// ---- glossary ----
function toggleHelp(show) {
  const overlay = $('help-overlay');
  const open = show === undefined ? overlay.classList.contains('hidden') : show;
  overlay.classList.toggle('hidden', !open);
}

$('help-btn').onclick = () => toggleHelp(true);
$('help-close').onclick = () => toggleHelp(false);
$('help-overlay').onclick = (e) => {
  if (e.target === $('help-overlay')) toggleHelp(false);
};

document.addEventListener('keydown', (e) => {
  const typing = /^(INPUT|TEXTAREA)$/.test((e.target || {}).tagName || '');
  if (e.key === 'Escape') toggleHelp(false);
  else if (e.key === '?' && !typing) toggleHelp();
});

$('force-sync').onclick = async () => {
  try {
    await post('/api/sync', {});
    await tick();
  } catch (err) {
    setSync(false, err.message);
  }
};

// Prefill username from ?username= so `--username` carries through.
const params = new URLSearchParams(location.search);
if (params.get('username')) {
  $('username').value = params.get('username');
  findLeagues();
}

// If the server was started with --league/--draft it is already connected.
api('/api/status')
  .then(async (s) => {
    if (!s.connected) return;
    state.status = s;
    state.board = (await api('/api/board')).players || [];
    $('setup').classList.add('hidden');
    $('board').classList.remove('hidden');
    renderHeader();
    await tick();
    startPolling();
  })
  .catch(() => {});
