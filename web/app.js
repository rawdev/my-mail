'use strict';

const $ = (sel) => document.querySelector(sel);
let accounts = [];
let providers = [];
let current = null;      // 선택된 계정 id
let currentMails = [];

// ---------------------------------------------------------------- 유틸

function toast(msg, isError) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'show' + (isError ? ' err' : '');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.className = ''; }, 3200);
}

async function api(url, options) {
  const res = await fetch(url, options);
  let data = {};
  try { data = await res.json(); } catch (e) { /* 본문 없음 */ }
  if (!res.ok) {
    const err = new Error(data.error || ('요청 실패 (' + res.status + ')'));
    err.data = data;
    throw err;
  }
  return data;
}

function spinner(el, text) {
  el.innerHTML = '';
  const d = document.createElement('div');
  d.className = 'spinner';
  d.textContent = text;
  el.appendChild(d);
}

function emptyMsg(el, text) {
  el.innerHTML = '';
  const d = document.createElement('div');
  d.className = 'empty';
  d.textContent = text;
  el.appendChild(d);
}

// 안읽음 배지: null = 아직 안 셈, 0 = 없음, N = N건
function countText(n) { return (n == null) ? '–' : String(n); }
function countClass(n) { return (n == null) ? 'unknown' : (n ? '' : 'zero'); }

// ---------------------------------------------------------------- 계정

async function loadAccounts() {
  const data = await api('/api/accounts');
  accounts = data.accounts;
  providers = data.providers;

  const ul = $('#accountList');
  ul.innerHTML = '';
  if (!accounts.length) {
    const li = document.createElement('li');
    li.textContent = '계정 없음';
    li.style.color = 'var(--muted)';
    li.style.cursor = 'default';
    ul.appendChild(li);
    return;
  }

  accounts.forEach((a) => {
    const li = document.createElement('li');
    li.dataset.id = a.id;
    li.setAttribute('role', 'button');
    li.tabIndex = 0;
    if (a.id === current) li.classList.add('active');

    const dot = document.createElement('span');
    dot.className = 'dot' + (a.session ? ' on' : '');
    dot.title = a.session ? '세션 저장됨' : '세션 없음';

    const col = document.createElement('div');
    col.className = 'col';
    const aid = document.createElement('div');
    aid.className = 'aid';
    aid.textContent = a.id;
    const mail = document.createElement('div');
    mail.className = 'amail';
    mail.textContent = a.email;
    col.append(aid, mail);

    const cnt = document.createElement('span');
    cnt.className = 'count ' + countClass(a.unread);
    cnt.dataset.acct = a.id;
    cnt.textContent = countText(a.unread);
    cnt.title = a.unread == null
      ? '안읽음 개수 미확인 — 상단 "안읽음 세기" 를 누르세요'
      : '안읽은 메일 ' + a.unread + '건 · 보관 ' + (a.archived || 0) + '건';

    const del = document.createElement('button');
    del.className = 'link';
    del.textContent = '✕';
    del.title = '계정 삭제';
    del.addEventListener('click', (ev) => { ev.stopPropagation(); removeAccount(a); });

    li.append(dot, col, cnt, del);
    li.addEventListener('click', () => selectAccount(a.id));
    li.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); selectAccount(a.id); }
    });
    ul.appendChild(li);
  });
}

async function removeAccount(a) {
  if (!confirm(a.email + ' 계정을 삭제할까요?\n저장된 비밀번호와 세션도 함께 삭제됩니다.')) return;
  try {
    await api('/api/accounts/' + encodeURIComponent(a.id), { method: 'DELETE' });
    if (current === a.id) {
      current = null;
      emptyMsg($('#listBody'), '왼쪽에서 계정을 선택하세요.');
      emptyMsg($('#readBody'), '메일을 클릭하면 본문이 여기에 표시됩니다.');
      $('#listTitle').textContent = '계정을 선택하세요';
      $('#listMeta').textContent = '';
    }
    await loadAccounts();
    toast('삭제했습니다.');
  } catch (e) {
    toast(e.message, true);
  }
}

// ---------------------------------------------------------------- 메일 목록

function selectAccount(id) {
  current = id;
  document.querySelectorAll('#accountList li').forEach((li) => {
    li.classList.toggle('active', li.dataset.id === id);
  });
  emptyMsg($('#readBody'), '메일을 클릭하면 본문이 여기에 표시됩니다.');
  $('#readTitle').textContent = '본문';
  loadMails();
}

async function loadMails() {
  if (!current) { toast('계정을 먼저 선택하세요.', true); return; }
  const acct = accounts.find((a) => a.id === current);
  $('#listTitle').textContent = acct ? acct.email : current;
  $('#listMeta').textContent = '';
  spinner($('#listBody'), '저장된 메일을 읽는 중…');

  const acctAtStart = current;
  try {
    // 화면에는 저장된 메일만 보여준다 — 메일 서버에 접속하지 않는다.
    const data = await api('/api/mails/cached?account=' + encodeURIComponent(current));
    if (acctAtStart !== current) return;
    currentMails = data.mails;
    if (!data.mails.length) {
      $('#listMeta').textContent = '';
      emptyMsg($('#listBody'),
        '저장된 메일이 없습니다. 상단 "⭳ 전체 가져오기" 를 눌러 받아오세요.');
      return;
    }
    renderMails(data);
  } catch (e) {
    emptyMsg($('#listBody'), e.message);
    toast(e.message, true);
  }
}

function renderMails(data) {
  const box = $('#listBody');
  box.innerHTML = '';
  if (!data.mails.length) {
    $('#listMeta').textContent = '';
    emptyMsg(box, data.mode === 'empty'
      ? '이 폴더에 메일이 없습니다.'
      : '메일 목록을 찾지 못했습니다. (셀렉터 확인 필요)');
    return;
  }
  const unread = data.mails.filter((m) => m.unread).length;
  const noBody = data.mails.filter((m) => m.hasBody === false).length;
  $('#listMeta').textContent = '저장된 메일 ' + data.mails.length + '건'
    + (unread ? ' · 안읽음 ' + unread : '')
    + (noBody ? ' · 본문없음 ' + noBody : '');

  data.mails.forEach((m) => {
    const row = document.createElement('div');
    row.className = 'mail' + (m.unread ? ' unread' : '');
    row.dataset.n = m.n;
    row.setAttribute('role', 'button');
    row.tabIndex = 0;

    const num = document.createElement('div');
    num.className = 'num';
    num.textContent = m.n;

    const from = document.createElement('div');
    from.className = 'from';
    from.textContent = m.sender || '(보낸사람 없음)';
    if (m.isNew) {
      const b = document.createElement('span');
      b.className = 'badge';
      b.textContent = 'NEW';
      from.appendChild(b);
    }

    const date = document.createElement('div');
    date.className = 'date';
    date.textContent = m.date || '';

    const subj = document.createElement('div');
    subj.className = 'subj';
    const stext = document.createElement('span');
    stext.className = 'stext';
    stext.textContent = m.subject || '(제목 없음)';
    subj.appendChild(stext);
    if (m.folder) {
      const tag = document.createElement('span');
      tag.className = 'ftag';
      tag.textContent = m.folder;
      subj.appendChild(tag);
    }

    row.append(num, from, date, subj);
    row.addEventListener('click', () => openMail(m.n, row, m.key));
    row.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openMail(m.n, row, m.key); }
    });
    box.appendChild(row);
  });
}

// ---------------------------------------------------------------- 본문

async function openMail(n, rowEl, key, fresh) {
  document.querySelectorAll('.mail').forEach((el) => el.classList.remove('active'));
  if (rowEl) rowEl.classList.add('active');

  $('#readTitle').textContent = n + '번 메일';
  spinner($('#readBody'), fresh ? '원본을 다시 가져오는 중…' : '본문을 여는 중…');

  try {
    const data = await api('/api/mail?account=' + encodeURIComponent(current) + '&n=' + n
      + (key ? '&key=' + encodeURIComponent(key) : '')
      + (fresh ? '&fresh=1' : ''));
    const box = $('#readBody');
    box.innerHTML = '';

    const head = document.createElement('div');
    head.className = 'read-head';
    const subj = document.createElement('div');
    subj.className = 'read-subject';
    subj.textContent = (data.subject || '(제목 없음)').replace(/\s+/g, ' ').trim();
    head.appendChild(subj);
    if (data.header) {
      const meta = document.createElement('div');
      meta.className = 'read-meta';
      meta.textContent = data.header;
      head.appendChild(meta);
    }

    // 본문은 원격 HTML 을 그대로 넣지 않고 텍스트로만 표시한다
    // (외부 이미지 추적 · 스크립트 삽입 방지)
    const pre = document.createElement('pre');
    pre.className = 'read-text';
    pre.textContent = data.bodyMissing
      ? '본문이 저장되지 않은 메일입니다.\n\n안읽은 메일은 열면 읽음 처리되기 때문에 '
        + '본문을 자동으로 받아두지 않습니다.\n아래 "원본 다시 가져오기" 를 누르면 '
        + '지금 받아옵니다. (해당 메일이 읽음 처리됩니다)'
      : (data.body || '(본문 없음)');

    const note = document.createElement('div');
    note.className = 'note';
    if (data.source === 'archive') {
      note.textContent = (data.bodyMissing ? '💾 저장된 본문 없음' : '💾 저장된 메일')
        + (data.folder ? ' · ' + data.folder : '')
        + (data.savedAt ? ' · ' + data.savedAt.replace('T', ' ').slice(0, 16) : '');
      const again = document.createElement('button');
      again.className = 'link';
      again.textContent = '원본 다시 가져오기';
      again.addEventListener('click', () => openMail(n, rowEl, key, true));
      note.append(' · ', again);
    } else {
      note.textContent = '※ 메일을 열었으므로 서버에서 읽음 처리됩니다. '
        + '본문은 안전을 위해 이미지·서식 없이 텍스트로만 표시합니다.';
    }

    box.append(head, pre, note);
    $('#readTitle').textContent = '본문';
  } catch (e) {
    emptyMsg($('#readBody'), e.message);
    toast(e.message, true);
  }
}

// ---------------------------------------------------------------- 로그인

let loginTarget = null;

function openLogin(aid) {
  loginTarget = aid;
  $('#loginMsg').textContent = '브라우저 창을 여는 중…';
  $('#loginDialog').showModal();

  api('/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ account: aid }),
  }).then((data) => {
    if (data.status === 'ok') {
      $('#loginDialog').close();
      toast('로그인되었습니다.');
      loadAccounts();
      loadMails();
    } else {
      $('#loginMsg').textContent =
        '열린 브라우저 창에서 로그인을 마무리하세요 (캡차 / 2단계 인증 등). '
        + '아이디와 비밀번호는 미리 채워져 있습니다. '
        + '메일함이 보이면 아래 "확인" 을 누르세요.';
    }
  }).catch((e) => {
    $('#loginMsg').textContent = e.message;
  });
}

$('#loginVerify').addEventListener('click', async () => {
  $('#loginMsg').textContent = '확인 중…';
  try {
    const data = await api('/api/login/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account: loginTarget }),
    });
    if (data.status === 'ok') {
      $('#loginDialog').close();
      toast('로그인되었습니다.');
      await loadAccounts();
      loadMails();
    } else {
      $('#loginMsg').textContent = '아직 로그인이 확인되지 않았습니다. 현재 주소: ' + data.url;
    }
  } catch (e) {
    $('#loginMsg').textContent = e.message;
  }
});

$('#loginCancel').addEventListener('click', () => $('#loginDialog').close());

// ---------------------------------------------------------------- 계정 추가

function guessProvider(email) {
  const d = (email.split('@')[1] || '').toLowerCase();
  if (d.includes('naver')) return 'naver';
  if (d.includes('daum') || d.includes('hanmail') || d.includes('kakao')) return 'daum';
  if (d.includes('zoho')) return 'zoho';
  return providers.includes('generic') ? 'generic' : providers[0];
}

$('#btnAdd').addEventListener('click', () => {
  const sel = $('#fProvider');
  sel.innerHTML = '';
  providers.forEach((p) => {
    const o = document.createElement('option');
    o.value = p; o.textContent = p;
    sel.appendChild(o);
  });
  $('#fEmail').value = '';
  $('#fPassword').value = '';
  $('#addDialog').showModal();
});

$('#fEmail').addEventListener('blur', () => {
  const g = guessProvider($('#fEmail').value);
  if (g) $('#fProvider').value = g;
});

$('#addForm').addEventListener('submit', async (ev) => {
  if (ev.submitter && ev.submitter.value !== 'ok') return;   // 취소
  ev.preventDefault();
  const body = {
    email: $('#fEmail').value.trim(),
    password: $('#fPassword').value,
    provider: $('#fProvider').value,
  };
  try {
    const data = await api('/api/accounts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    $('#addDialog').close();
    toast('추가했습니다: ' + data.id);
    await loadAccounts();
    selectAccount(data.id);
  } catch (e) {
    toast(e.message, true);
  }
});

// ---------------------------------------------------------------- 로컬 보관 / 안읽음 세기

function setArchiveStatus(text) { $('#archiveStatus').textContent = text || ''; }

function refreshAccountBadges() {
  api('/api/accounts').then((d) => {
    accounts = d.accounts;
    (d.accounts || []).forEach((a) => {
      const el = document.querySelector('#accountList .count[data-acct="' + CSS.escape(a.id) + '"]');
      if (!el) return;
      el.textContent = countText(a.unread);
      el.className = 'count ' + countClass(a.unread);
      el.title = a.unread == null
        ? '안읽음 개수 미확인 — "전체 가져오기" 를 실행하세요'
        : '안읽은 메일 ' + a.unread + '건 · 저장 ' + (a.archived || 0) + '건';
    });
  }).catch(() => {});
}

$('#btnReload').addEventListener('click', () => { if (current) loadMails(); });

// ---------------------------------------------------------------- 전체 가져오기

let syncing = false;
let syncAbort = false;

function showSyncBar(show) {
  $('#syncBar').hidden = !show;
  if (!show) { $('#syncFill').style.width = '0'; $('#syncText').textContent = ''; }
}

/** 등록된 모든 계정의 모든 편지함을 순서대로 가져온다(+없는 메일 보관, 안읽음 집계). */
async function syncAll() {
  if (syncing) return;
  if (!accounts.length) return;

  syncing = true;
  syncAbort = false;
  showSyncBar(true);
  $('#btnSync').disabled = true;

  // 1단계: 계정별 편지함 목록을 모아 전체 작업 수를 만든다
  const jobs = [];
  for (const a of accounts) {
    if (syncAbort) break;
    $('#syncText').textContent = a.email + ' — 편지함 목록 확인 중…';
    try {
      const d = await api('/api/folders?account=' + encodeURIComponent(a.id));
      const fs = (d.folders || []);
      if (fs.length) fs.forEach((f) => jobs.push({ acct: a, folder: f.name }));
      else jobs.push({ acct: a, folder: '' });          // 폴더 미지원 서비스
    } catch (e) {
      // 로그인 안 된 계정은 건너뛰고 계속 (전체 수집이 멈추지 않도록)
      toast(a.email + ': ' + e.message, true);
    }
  }

  // 2단계: 편지함을 하나씩 수집
  let done = 0, newMails = 0, bodies = 0, failed = 0;
  for (const job of jobs) {
    if (syncAbort) break;
    done++;
    $('#syncFill').style.width = Math.round((done / jobs.length) * 100) + '%';
    $('#syncText').textContent =
      '전체 가져오기 ' + done + '/' + jobs.length + ' — '
      + job.acct.id + ' / ' + (job.folder || '받은 편지함');
    try {
      const r = await api('/api/sync/folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account: job.acct.id, folder: job.folder }),
      });
      newMails += r.new || 0;
      bodies += r.bodies || 0;
    } catch (e) {
      failed++;
    }
  }

  refreshAccountBadges();
  syncing = false;
  $('#btnSync').disabled = false;
  showSyncBar(false);

  if (syncAbort) {
    toast('전체 가져오기를 중지했습니다. (' + done + '/' + jobs.length + ')');
  } else {
    api('/api/sync/done', { method: 'POST' }).catch(() => {});
    toast('전체 가져오기 완료 — 새 메일 ' + newMails + '건, 본문 ' + bodies + '건'
      + (failed ? ', 실패 ' + failed + '개 편지함' : ''));
    if (current) loadMails();            // 받은 메일을 화면에 반영
  }
}

$('#btnSync').addEventListener('click', syncAll);
$('#syncStop').addEventListener('click', () => {
  syncAbort = true;
  $('#syncText').textContent = '중지하는 중… (진행 중인 편지함까지만 끝냅니다)';
});

// ---------------------------------------------------------------- 시작

(async function start() {
  try {
    await loadAccounts();
  } catch (e) {
    toast(e.message, true);
    return;
  }
  try {
    const c = await api('/api/config');
    // 서버가 켜진 뒤 처음 열었을 때만 자동 수집 (새로고침마다 반복하지 않음)
    if (c.syncOnStart && !c.syncedThisBoot && accounts.length) syncAll();
  } catch (e) { /* 설정을 못 읽으면 자동 수집만 건너뛴다 */ }
})();
