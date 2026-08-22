'use strict';

const $ = (sel) => document.querySelector(sel);
let accounts = [];
let providers = [];
let current = null;      // 선택된 계정 id
let currentMails = [];
let currentFolder = '';  // 선택된 폴더 이름 ('' = 기본 = 받은편지함)

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

    const del = document.createElement('button');
    del.className = 'link';
    del.textContent = '✕';
    del.title = '계정 삭제';
    del.addEventListener('click', (ev) => { ev.stopPropagation(); removeAccount(a); });

    li.append(dot, col, del);
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
      currentFolder = '';
      emptyMsg($('#folderList'), '계정을 선택하세요.');
      emptyMsg($('#listBody'), '편지함을 선택하세요.');
      emptyMsg($('#readBody'), '메일을 클릭하면 본문이 여기에 표시됩니다.');
      $('#listTitle').textContent = '편지함을 선택하세요';
      $('#listMeta').textContent = '';
      $('#folderMeta').textContent = '';
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
  currentFolder = '';
  document.querySelectorAll('#accountList li').forEach((li) => {
    li.classList.toggle('active', li.dataset.id === id);
  });
  emptyMsg($('#readBody'), '메일을 클릭하면 본문이 여기에 표시됩니다.');
  $('#readTitle').textContent = '본문';
  emptyMsg($('#listBody'), '편지함을 선택하세요.');
  $('#listTitle').textContent = '편지함을 선택하세요';
  $('#listMeta').textContent = '';
  spinner($('#folderList'), '편지함 목록을 불러오는 중…');
  $('#folderMeta').textContent = '';
  loadFolders();
}

// 폴더 이름 앞에 붙일 아이콘 (이름으로 대충 매칭, 없으면 기본 폴더 아이콘)
function folderIcon(name) {
  const n = name.toLowerCase();
  if (/받은|inbox/.test(n)) return '📥';
  if (/보낸|보냄|sent/.test(n)) return '📤';
  if (/초안|draft/.test(n)) return '📝';
  if (/스팸|spam|junk/.test(n)) return '🚫';
  if (/휴지통|trash|삭제/.test(n)) return '🗑️';
  if (/보관|archive/.test(n)) return '📦';
  if (/템플릿|template/.test(n)) return '📋';
  if (/알림|notification/.test(n)) return '🔔';
  if (/newsletter|뉴스/.test(n)) return '📰';
  return '📁';
}

async function loadFolders() {
  const acct = current;
  const box = $('#folderList');
  try {
    const data = await api('/api/folders?account=' + encodeURIComponent(acct));
    if (acct !== current) return;              // 그 사이 계정이 바뀌었으면 무시

    const folders = data.folders || [];
    if (!folders.length) {
      // 폴더를 지원하지 않는 서비스 — 기본 편지함만 바로 연다
      emptyMsg(box, '이 서비스는 편지함 목록을 지원하지 않습니다.');
      $('#folderMeta').textContent = '';
      loadMails();
      return;
    }

    box.innerHTML = '';
    $('#folderMeta').textContent = folders.length + '개';
    folders.forEach((f) => {
      const el = document.createElement('div');
      el.className = 'folder';
      el.dataset.name = f.name;
      el.setAttribute('role', 'button');
      el.tabIndex = 0;

      const icon = document.createElement('span');
      icon.className = 'ficon';
      icon.textContent = folderIcon(f.name);
      const nm = document.createElement('span');
      nm.className = 'fname';
      nm.textContent = f.name;
      el.append(icon, nm);

      const open = () => selectFolder(f.name);
      el.addEventListener('click', open);
      el.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); }
      });
      box.appendChild(el);
    });

    // 서버가 알려준 현재 폴더(보통 받은편지함)를 바로 연다
    const start = folders.find((f) => f.current) || folders[0];
    selectFolder(start.name);
  } catch (e) {
    if (e.data && e.data.needLogin) {
      emptyMsg(box, '로그인이 필요합니다.');
      openLogin(acct);
    } else {
      emptyMsg(box, e.message);
    }
  }
}

function selectFolder(name) {
  currentFolder = name;
  document.querySelectorAll('#folderList .folder').forEach((el) => {
    el.classList.toggle('active', el.dataset.name === name);
  });
  emptyMsg($('#readBody'), '메일을 클릭하면 본문이 여기에 표시됩니다.');
  loadMails();
}

async function loadMails() {
  if (!current) { toast('계정을 먼저 선택하세요.', true); return; }
  $('#listTitle').textContent = currentFolder || '받은 편지함';
  $('#listMeta').textContent = '';
  spinner($('#listBody'), '메일을 가져오는 중… (브라우저 세션을 여느라 몇 초 걸립니다)');

  try {
    const data = await api('/api/mails?account=' + encodeURIComponent(current)
      + (currentFolder ? '&folder=' + encodeURIComponent(currentFolder) : ''));
    currentMails = data.mails;
    renderMails(data);
  } catch (e) {
    if (e.data && e.data.needLogin) {
      emptyMsg($('#listBody'), '로그인이 필요합니다.');
      openLogin(current);
    } else {
      emptyMsg($('#listBody'), e.message);
      toast(e.message, true);
    }
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
  $('#listMeta').textContent =
    data.mails.length + '건 · 안읽음 ' + unread + ' · ' + data.mode;

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
    subj.textContent = m.subject || '(제목 없음)';

    row.append(num, from, date, subj);
    row.addEventListener('click', () => openMail(m.n, row));
    row.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openMail(m.n, row); }
    });
    box.appendChild(row);
  });
}

// ---------------------------------------------------------------- 본문

async function openMail(n, rowEl) {
  document.querySelectorAll('.mail').forEach((el) => el.classList.remove('active'));
  if (rowEl) rowEl.classList.add('active');

  $('#readTitle').textContent = n + '번 메일';
  spinner($('#readBody'), '본문을 여는 중…');

  try {
    const data = await api('/api/mail?account=' + encodeURIComponent(current) + '&n=' + n
      + (currentFolder ? '&folder=' + encodeURIComponent(currentFolder) : ''));
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
    pre.textContent = data.body || '(본문 없음)';

    const note = document.createElement('div');
    note.className = 'note';
    note.textContent = '※ 메일을 열었으므로 서버에서 읽음 처리됩니다. '
      + '본문은 안전을 위해 이미지·서식 없이 텍스트로만 표시합니다.';

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

$('#btnReload').addEventListener('click', () => { if (current) loadMails(); });

// ---------------------------------------------------------------- 시작

loadAccounts().catch((e) => toast(e.message, true));
