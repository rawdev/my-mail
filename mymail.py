# -*- coding: utf-8 -*-
"""
mymail.py — POP/IMAP을 지원하지 않는 웹메일들을 브라우저 자동화로 읽어오는 멀티 계정 콘솔 도구.

계정 관리:
  python mymail.py add                # 계정 추가 (이메일/비밀번호를 콘솔에서 입력)
  python mymail.py list               # 등록된 계정 목록
  python mymail.py remove <계정ID>    # 계정 삭제 (저장된 비밀번호/세션도 삭제)

메일 확인:
  python mymail.py fetch                     # 모든 계정의 최신 메일 출력
  python mymail.py fetch --account <계정ID>  # 특정 계정만
  python mymail.py fetch --watch 60          # 60초마다 전 계정 새 메일 감시
  python mymail.py login --account <계정ID>  # 자동 로그인이 막힐 때(캡차/2단계인증) 수동 로그인

비밀번호는 accounts.json 에 평문으로 저장됩니다 (이 파일을 외부에 공유하지 마세요).
계정마다 별도의 브라우저 프로필(user_data/<계정ID>/)을 사용해 세션이 섞이지 않습니다.
"""
import argparse
import getpass
import hashlib
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
ACCOUNTS_PATH = BASE_DIR / "accounts.json"

console = Console()

# ---------------------------------------------------------------- 설정/계정 저장소

def load_config():
    if not CONFIG_PATH.exists():
        console.print(f"[red]config.json 이 없습니다: {CONFIG_PATH}[/red]")
        sys.exit(1)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_accounts():
    if ACCOUNTS_PATH.exists():
        with open(ACCOUNTS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_accounts(accounts):
    ACCOUNTS_PATH.write_text(
        json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")


def get_provider(cfg, name):
    providers = cfg.get("providers", {})
    if name not in providers:
        console.print(f"[red]알 수 없는 provider: {name}[/red] (가능: {', '.join(providers)})")
        sys.exit(1)
    p = dict(providers[name])
    p["name"] = name
    return p


def make_account_id(email, accounts):
    base = re.sub(r"[^a-zA-Z0-9]+", "-", email.split("@")[0]).strip("-").lower() or "acct"
    aid, n = base, 2
    existing = {a["id"] for a in accounts}
    while aid in existing:
        aid = f"{base}{n}"
        n += 1
    return aid


def find_account(accounts, aid):
    for a in accounts:
        if a["id"] == aid or a["email"] == aid:
            return a
    return None

# ---------------------------------------------------------------- 브라우저

def profile_dir(cfg, account):
    d = BASE_DIR / cfg.get("user_data_dir", "user_data") / account["id"]
    d.mkdir(parents=True, exist_ok=True)
    return d


# headless 크롬은 UA 에 'HeadlessChrome' 가 박혀 있어 일부 서비스가 세션을 거부한다.
# headed 로 만든 세션을 headless 에서 그대로 쓰기 위해 UA 를 일반 크롬으로 맞춘다.
NORMAL_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")


def open_context(pw, cfg, account, headed):
    headless = (not headed) and cfg.get("headless", True)
    return pw.chromium.launch_persistent_context(
        str(profile_dir(cfg, account)),
        headless=headless,
        user_agent=NORMAL_UA if headless else None,
        viewport={"width": 1400, "height": 900},
        locale="ko-KR",
        args=["--disable-blink-features=AutomationControlled"],
    )


def wait_for_settle(page, timeout=15000, quiet=1500):
    """리다이렉트 연쇄가 끝날 때까지(URL이 quiet 동안 그대로일 때까지) 대기."""
    last, stable, waited, step = page.url, 0, 0, 500
    while waited < timeout:
        page.wait_for_timeout(step)
        waited += step
        if page.url == last:
            stable += step
            if stable >= quiet:
                return
        else:
            last, stable = page.url, 0


def has_visible_password_field(page):
    try:
        return bool(page.evaluate(
            "() => [...document.querySelectorAll(\"input[type='password']\")]"
            ".some(e => e.offsetParent !== null)"))
    except Exception:
        return False


def is_login_page(page, provider):
    url = page.url.lower()
    return any(m.lower() in url for m in provider.get("login_redirect_markers", []))


def is_logged_in(page, provider):
    """'로그인 페이지가 아님'이 아니라 '실제로 메일함에 도달했는지'로 판정.

    mail.zoho.com 처럼 로그아웃 상태에서 마케팅/가입 페이지로 튕기는 경우가 있어서,
    로그인 페이지가 아니라는 사실만으로 성공이라고 볼 수 없다.
    """
    url = page.url.lower()
    markers = provider.get("logged_in_markers") or []
    if markers:
        return any(m.lower() in url for m in markers)
    return not is_login_page(page, provider) and not has_visible_password_field(page)


def try_auto_login(page, provider, account):
    """저장된 비밀번호로 로그인 폼 자동 입력. 캡차/2단계 인증이 뜨면 False."""
    form = provider.get("login_form") or {}
    password = account.get("password")
    if not form.get("id") or not password:
        return False
    # 안전장치: 로그인 페이지로 확인된 곳에서만 폼을 채운다.
    # (Zoho 마케팅 페이지에도 '가입용' 비밀번호 입력칸이 있어 잘못 입력될 수 있음)
    if not is_login_page(page, provider):
        return False
    try:
        page.wait_for_selector(form["id"], timeout=8000)
        page.fill(form["id"], account["email"])
        # Zoho처럼 아이디 → 다음 → 비밀번호로 나뉜 2단계 폼 지원:
        # 비밀번호 칸이 아직 안 보이면 먼저 '다음'을 누르고 나타날 때까지 대기
        pw_el = page.query_selector(form["pw"])
        if not (pw_el and pw_el.is_visible()):
            page.click(form["submit"])
            page.wait_for_selector(form["pw"], state="visible", timeout=8000)
            page.wait_for_timeout(500)
        page.fill(form["pw"], password)
        page.click(form["submit"])
        page.wait_for_load_state("domcontentloaded")
        wait_for_settle(page)
    except PWTimeout:
        return False
    except Exception as e:
        console.print(f"[yellow]자동 로그인 중 오류: {e}[/yellow]")
        return False
    return is_logged_in(page, provider)


def ensure_logged_in(page, provider, account):
    """mail_url 접속 후 로그인 상태 보장. 성공 시 True."""
    page.goto(provider["mail_url"], wait_until="domcontentloaded")
    wait_for_settle(page)
    if is_logged_in(page, provider):
        return True

    console.print(f"[dim]{account['id']}: 세션 없음/만료 → 자동 로그인 시도...[/dim]")
    # 로그인 페이지가 아닌 곳(마케팅 페이지 등)으로 튕겼으면 로그인 주소로 직접 이동
    if not is_login_page(page, provider) and provider.get("login_url"):
        page.goto(provider["login_url"], wait_until="domcontentloaded")
        wait_for_settle(page)

    if try_auto_login(page, provider, account):
        page.goto(provider["mail_url"], wait_until="domcontentloaded")
        wait_for_settle(page)
        if is_logged_in(page, provider):
            console.print(f"[green]{account['id']}: 자동 로그인 성공[/green]")
            return True

    console.print(
        f"[red]{account['id']}: 자동 로그인 실패 (캡차/2단계 인증일 수 있음). "
        f"`python mymail.py login --account {account['id']}` 로 수동 로그인하세요.[/red]")
    console.print(f"[dim]  현재 주소: {page.url[:100]}[/dim]")
    return False

# ---------------------------------------------------------------- 메일 추출

def row_is_bold(row, subject_sel):
    """행(또는 제목 요소)이 굵은 글씨인지 = 안읽음 여부."""
    try:
        return bool(row.evaluate(
            "(el, sel) => { const t = (sel && el.querySelector(sel)) || el;"
            " return parseInt(getComputedStyle(t).fontWeight, 10) >= 600; }",
            subject_sel))
    except Exception:
        return False


def extract_with_selectors(page, provider, limit):
    rows = page.query_selector_all(provider["row"])
    mails = []
    for row in rows[:limit]:
        def text_of(sel):
            if not sel:
                return ""
            el = row.query_selector(sel)
            return el.inner_text().strip().replace("\n", " ") if el else ""

        cls = (row.get_attribute("class") or "")
        unread_cls = provider.get("unread_class")
        if unread_cls:
            unread = unread_cls in cls
        else:
            # Zoho 처럼 안읽음 전용 클래스가 없는 서비스는 굵은 글씨로 구분한다
            unread = row_is_bold(row, provider.get("subject"))
        mails.append({
            "sender": text_of(provider.get("sender")),
            "subject": text_of(provider.get("subject")),
            "date": text_of(provider.get("date")),
            "unread": unread,
        })
    return [m for m in mails if m["subject"] or m["sender"]]


HEURISTIC_JS = """
() => {
  const candidates = [];
  const seenParents = new Set();
  for (const el of document.querySelectorAll('body *')) {
    const p = el.parentElement;
    if (!p || seenParents.has(p)) continue;
    seenParents.add(p);
    const groups = {};
    for (const c of p.children) (groups[c.tagName] = groups[c.tagName] || []).push(c);
    for (const els of Object.values(groups)) {
      if (els.length < 5) continue;
      const txts = els.map(e => (e.innerText || '').trim()).filter(t => t.length > 10);
      if (txts.length < 5) continue;
      const avgLen = txts.reduce((a, t) => a + t.length, 0) / txts.length;
      if (avgLen < 15 || avgLen > 500) continue;
      candidates.push({ score: els.length * Math.min(avgLen, 120), els });
    }
  }
  if (!candidates.length) return [];
  candidates.sort((a, b) => b.score - a.score);
  return candidates[0].els.map(e => ({
    parts: (e.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean),
    bold: parseInt(getComputedStyle(e).fontWeight, 10) >= 600,
    cls: e.className || '',
  }));
}
"""


def extract_heuristic(page, limit):
    mails = []
    for r in page.evaluate(HEURISTIC_JS)[:limit]:
        parts = r.get("parts", [])
        if not parts:
            continue
        sender = parts[0] if len(parts) > 1 else ""
        date = parts[-1] if len(parts) > 2 else ""
        middle = parts[1:-1] if len(parts) > 2 else parts[-1:]
        mails.append({
            "sender": sender,
            "subject": " / ".join(middle)[:120],
            "date": date,
            "unread": r.get("bold", False) or "unread" in r.get("cls", ""),
        })
    return mails


def fetch_mails(page, provider, limit):
    if provider.get("row"):
        try:
            # SPA 는 로그인 직후 목록 렌더링이 늦다. 행이 나타날 때까지 넉넉히 기다리고,
            # 나머지 행이 채워지도록 잠깐 더 대기한다.
            page.wait_for_selector(provider["row"], timeout=25000)
            page.wait_for_timeout(1500)
            mails = extract_with_selectors(page, provider, limit)
            if mails:
                return mails, "selector"
        except PWTimeout:
            pass
        console.print("[yellow]프리셋 셀렉터로 못 찾아 휴리스틱 추출기로 전환합니다. "
                      "(`inspect` 로 셀렉터를 조정할 수 있습니다)[/yellow]")
    page.wait_for_timeout(6000)
    return extract_heuristic(page, limit), "heuristic"

def _text_of(page, sel):
    if not sel:
        return ""
    el = page.query_selector(sel)
    return el.inner_text().strip() if el else ""


LARGEST_BLOCK_JS = """
() => {
  // 본문 셀렉터를 모르는 서비스용: 가장 긴 텍스트를 가진 '말단에 가까운' 블록을 고른다.
  let best = null, bestLen = 0;
  for (const el of document.querySelectorAll('div,td,section,article,pre')) {
    const t = (el.innerText || '').trim();
    if (t.length < 40) continue;
    const kids = [...el.children].filter(c => (c.innerText || '').trim().length > 40);
    if (kids.length > 1) continue;
    if (t.length > bestLen) { best = t; bestLen = t.length; }
  }
  return best || '';
}
"""


def open_mail_rows(page, provider):
    """메일 목록 행 핸들을 반환. row 셀렉터가 없으면 빈 리스트."""
    if not provider.get("row"):
        return []
    try:
        page.wait_for_selector(provider["row"], timeout=25000)
        page.wait_for_timeout(1500)
    except PWTimeout:
        return []
    return page.query_selector_all(provider["row"])


def read_opened_mail(page, provider):
    """열려 있는 메일에서 (제목, 헤더, 본문) 추출."""
    view = provider.get("view") or {}
    body_sel = view.get("body")
    if body_sel:
        try:
            page.wait_for_selector(body_sel, timeout=20000)
        except PWTimeout:
            pass
        page.wait_for_timeout(1500)
        return (_text_of(page, view.get("subject")),
                _text_of(page, view.get("header")),
                _text_of(page, body_sel))
    page.wait_for_timeout(4000)
    return "", "", page.evaluate(LARGEST_BLOCK_JS)


# ---------------------------------------------------------------- 출력/기록

def mail_key(m):
    return hashlib.md5(f"{m['sender']}|{m['subject']}|{m['date']}".encode("utf-8")).hexdigest()


def render_table(mails, title, numbers=None):
    table = Table(title=title, expand=True, show_lines=False)
    table.add_column("#", width=3, justify="right", style="dim")
    table.add_column("", width=2)
    # 한글은 한 글자가 2칸이라 쉽게 줄바꿈된다. 메일 1건 = 1줄이 되도록 감김을 막는다.
    table.add_column("보낸사람", max_width=22, overflow="ellipsis", no_wrap=True)
    table.add_column("제목", ratio=1, overflow="ellipsis", no_wrap=True)
    table.add_column("날짜", max_width=22, overflow="ellipsis", no_wrap=True)
    nums = numbers or list(range(1, len(mails) + 1))
    for n, m in zip(nums, mails):
        mark = "●" if m["unread"] else ""
        style = "bold cyan" if m["unread"] else None
        table.add_row(str(n), mark, m["sender"], m["subject"], m["date"], style=style)
    console.print(table)


def seen_path(account):
    return BASE_DIR / f".seen_{account['id']}.json"


def load_seen(account):
    p = seen_path(account)
    if p.exists():
        try:
            return set(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()


def save_seen(account, keys):
    seen_path(account).write_text(json.dumps(sorted(keys)), encoding="utf-8")

# ---------------------------------------------------------------- 계정 관리 명령

def cmd_add(cfg):
    accounts = load_accounts()
    providers = list(cfg.get("providers", {}).keys())
    console.print(Panel("새 계정을 등록합니다.", border_style="green"))

    email = input("이메일 주소: ").strip()
    if not email or "@" not in email:
        console.print("[red]올바른 이메일 주소를 입력하세요.[/red]")
        sys.exit(1)
    if find_account(accounts, email):
        console.print("[red]이미 등록된 이메일입니다.[/red]")
        sys.exit(1)

    console.print(f"메일 서비스 종류: {', '.join(providers)}")
    domain = email.split("@")[1].lower()
    guess = ("naver" if "naver" in domain else
             "daum" if ("daum" in domain or "hanmail" in domain or "kakao" in domain) else
             "zoho" if "zoho" in domain else
             "generic")
    provider = input(f"provider [{guess}]: ").strip() or guess
    if provider not in providers:
        console.print(f"[red]알 수 없는 provider 입니다. config.json 의 providers 에 먼저 추가하세요.[/red]")
        sys.exit(1)

    password = getpass.getpass("비밀번호 (화면에 표시되지 않음): ")
    if not password:
        console.print("[red]비밀번호가 비어 있습니다.[/red]")
        sys.exit(1)

    aid = make_account_id(email, accounts)
    accounts.append({"id": aid, "email": email, "provider": provider, "password": password})
    save_accounts(accounts)
    console.print(f"[green]등록 완료: [bold]{aid}[/bold] ({email}, {provider})[/green]")
    console.print("[dim]비밀번호는 accounts.json 에 평문으로 저장됩니다. 이 파일을 공유하지 마세요.[/dim]")
    console.print(f"바로 확인: [bold]python mymail.py fetch --account {aid}[/bold]")


def cmd_list(cfg):
    accounts = load_accounts()
    if not accounts:
        console.print("등록된 계정이 없습니다. `python mymail.py add` 로 추가하세요.")
        return
    table = Table(title=f"등록된 계정 {len(accounts)}개")
    table.add_column("ID", style="bold")
    table.add_column("이메일")
    table.add_column("provider")
    table.add_column("세션")
    for a in accounts:
        has_session = (BASE_DIR / cfg.get("user_data_dir", "user_data") / a["id"] / "Default").exists()
        table.add_row(a["id"], a["email"], a["provider"], "저장됨" if has_session else "-")
    console.print(table)


def cmd_remove(cfg, aid):
    accounts = load_accounts()
    acct = find_account(accounts, aid)
    if not acct:
        console.print(f"[red]계정을 찾을 수 없습니다: {aid}[/red]")
        sys.exit(1)
    confirm = input(f"'{acct['email']}' 계정을 삭제할까요? 저장된 비밀번호와 세션도 함께 삭제됩니다 [y/N]: ")
    if confirm.strip().lower() != "y":
        console.print("취소했습니다.")
        return
    import shutil
    shutil.rmtree(BASE_DIR / cfg.get("user_data_dir", "user_data") / acct["id"], ignore_errors=True)
    seen_path(acct).unlink(missing_ok=True)
    save_accounts([a for a in accounts if a["id"] != acct["id"]])
    console.print(f"[green]삭제 완료: {acct['id']}[/green]")

# ---------------------------------------------------------------- 로그인/조회 명령

def pick_accounts(args):
    accounts = load_accounts()
    if not accounts:
        console.print("[red]등록된 계정이 없습니다. `python mymail.py add` 로 먼저 추가하세요.[/red]")
        sys.exit(1)
    if args.account:
        acct = find_account(accounts, args.account)
        if not acct:
            console.print(f"[red]계정을 찾을 수 없습니다: {args.account}[/red]")
            sys.exit(1)
        return [acct]
    return accounts


def cmd_login(cfg, args):
    for acct in pick_accounts(args):
        provider = get_provider(cfg, acct["provider"])
        with sync_playwright() as pw:
            ctx = open_context(pw, cfg, acct, headed=True)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(provider["mail_url"], wait_until="domcontentloaded")
                wait_for_settle(page)
                if is_logged_in(page, provider):
                    console.print(f"[green]{acct['id']}: 이미 로그인되어 있습니다.[/green]")
                    continue

                # 로그아웃 상태에서 로그인 페이지가 아닌 곳으로 튕기는 서비스가 있어
                # (Zoho → 마케팅/가입 페이지) 로그인 주소로 직접 이동시킨다.
                if not is_login_page(page, provider) and provider.get("login_url"):
                    page.goto(provider["login_url"], wait_until="domcontentloaded")
                    wait_for_settle(page)

                # 아이디/비번은 미리 채워주고, 캡차·2단계 인증만 사람이 처리
                try_auto_login(page, provider, acct)

                if not is_logged_in(page, provider):
                    console.print(Panel(
                        f"[bold]{acct['email']}[/bold]\n"
                        "브라우저 창에서 로그인을 마무리하세요 (캡차/2단계 인증 등).\n"
                        "메일함이 보이면 이 콘솔에서 [bold]Enter[/bold] 를 누르세요.",
                        title=f"수동 로그인 — {acct['id']}", border_style="green"))
                    input()

                # 최종 확인: 정말로 메일함에 접근되는지 다시 열어본다
                page.goto(provider["mail_url"], wait_until="domcontentloaded")
                wait_for_settle(page)
                if is_logged_in(page, provider):
                    console.print(f"[green]{acct['id']}: 로그인 성공, 세션이 저장되었습니다.[/green]")
                else:
                    console.print(f"[red]{acct['id']}: 로그인이 확인되지 않았습니다. "
                                  f"다시 시도하거나 주소를 확인하세요.[/red]")
                    console.print(f"[dim]  현재 주소: {page.url[:100]}[/dim]")
            finally:
                ctx.close()


def cmd_fetch(cfg, args):
    targets = pick_accounts(args)
    limit = args.limit or cfg.get("limit", 20)
    with sync_playwright() as pw:
        first_round = True
        try:
            while True:
                for acct in targets:
                    provider = get_provider(cfg, acct["provider"])
                    ctx = open_context(pw, cfg, acct, headed=args.headed)
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                    try:
                        if not ensure_logged_in(page, provider, acct):
                            continue
                        mails, mode = fetch_mails(page, provider, limit)
                        if not mails:
                            console.print(f"[yellow]{acct['id']}: 메일을 찾지 못했습니다. "
                                          f"`inspect --account {acct['id']}` 로 확인해보세요.[/yellow]")
                            continue
                        now = time.strftime("%H:%M:%S")
                        seen = load_seen(acct)
                        if first_round or not args.watch:
                            render_table(mails, f"📬 {acct['email']} — 최신 {len(mails)}건 ({mode}, {now})")
                        else:
                            pairs = [(i, m) for i, m in enumerate(mails, 1)
                                     if mail_key(m) not in seen]
                            if pairs:
                                nums = [i for i, _ in pairs]
                                new = [m for _, m in pairs]
                                console.print(f"[bold green]🔔 {acct['email']} 새 메일 {len(new)}건! ({now})[/bold green]")
                                render_table(new, f"새 메일 — {acct['email']}", numbers=nums)
                            else:
                                console.print(f"[dim]{now} {acct['id']}: 새 메일 없음[/dim]")
                        save_seen(acct, seen | {mail_key(m) for m in mails})
                    finally:
                        ctx.close()
                first_round = False
                if not args.watch:
                    break
                time.sleep(args.watch)
        except KeyboardInterrupt:
            console.print("\n[dim]감시를 종료합니다.[/dim]")


def cmd_read(cfg, args):
    accounts = pick_accounts(args)
    if len(accounts) > 1:
        console.print("[yellow]계정이 여러 개입니다. --account <계정ID> 로 지정하세요.[/yellow]")
        cmd_list(cfg)
        return
    acct = accounts[0]
    provider = get_provider(cfg, acct["provider"])
    if not provider.get("row"):
        console.print(f"[red]{acct['provider']} 는 목록 셀렉터(row)가 설정되어 있지 않아 "
                      f"본문 열기를 지원하지 않습니다. config.json 을 먼저 채우세요.[/red]")
        return

    with sync_playwright() as pw:
        ctx = open_context(pw, cfg, acct, headed=args.headed)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            if not ensure_logged_in(page, provider, acct):
                return
            rows = open_mail_rows(page, provider)
            if not rows:
                console.print("[yellow]메일 목록을 찾지 못했습니다.[/yellow]")
                return
            idx = args.number - 1
            if not (0 <= idx < len(rows)):
                console.print(f"[red]{args.number} 번 메일이 없습니다. "
                              f"현재 목록에는 1~{len(rows)} 번이 있습니다.[/red]")
                return

            rows[idx].click()
            subject, header, body = read_opened_mail(page, provider)

            if not (subject or header or body):
                console.print("[yellow]본문을 찾지 못했습니다. "
                              "config.json 의 view.body 셀렉터를 확인하세요.[/yellow]")
                return

            title = subject.replace(chr(10), " ").strip() or f"{args.number} 번 메일"
            if header:
                console.print(Panel(header, title="보낸사람 / 받는사람 / 날짜",
                                    border_style="blue"))
            console.print(Panel.fit(title, border_style="cyan"))
            console.print()
            # 메일 본문에 [..] 같은 문자가 있어도 서식으로 해석되지 않도록 markup 끔
            console.print(body or "(본문 없음)", markup=False, highlight=False)
            console.print()
            console.print("[dim]※ 메일을 열었으므로 서버에서 '읽음' 처리됩니다.[/dim]")
        finally:
            ctx.close()


def cmd_inspect(cfg, args):
    for acct in pick_accounts(args):
        provider = get_provider(cfg, acct["provider"])
        with sync_playwright() as pw:
            ctx = open_context(pw, cfg, acct, headed=args.headed)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                if not ensure_logged_in(page, provider, acct):
                    continue
                page.wait_for_timeout(2000)
                rows = page.evaluate(HEURISTIC_JS)
                console.print(f"[bold]{acct['email']} — 휴리스틱이 찾은 반복 목록: {len(rows)}행[/bold]\n")
                for i, r in enumerate(rows[:10]):
                    console.print(Panel(
                        f"[dim]class:[/dim] {r.get('cls','')}\n[dim]텍스트 조각:[/dim] {r.get('parts')}",
                        title=f"행 {i+1}", border_style="blue"))
            finally:
                ctx.close()

# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="멀티 계정 웹메일 → 로컬 콘솔 메일 뷰어")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("add", help="계정 추가 (이메일/비밀번호 입력)")
    sub.add_parser("list", help="계정 목록")
    p_rm = sub.add_parser("remove", help="계정 삭제")
    p_rm.add_argument("account_id")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--account", help="계정 ID 또는 이메일 (생략 시 전체)")
    common.add_argument("--headed", action="store_true", help="브라우저 창을 보이게 실행")

    sub.add_parser("login", parents=[common], help="세션 로그인 (자동 시도, 캡차/2FA는 수동)")
    p_fetch = sub.add_parser("fetch", parents=[common], help="최신 메일 출력")
    p_fetch.add_argument("--limit", type=int, help="가져올 메일 수")
    p_fetch.add_argument("--watch", type=int, metavar="초", help="N초마다 새 메일 감시")
    p_read = sub.add_parser("read", parents=[common], help="메일 본문 읽기")
    p_read.add_argument("number", type=int, help="목록에서의 번호 (1부터)")
    sub.add_parser("inspect", parents=[common], help="셀렉터 튜닝용 구조 덤프")

    args = ap.parse_args()
    cfg = load_config()

    if args.command == "add":
        cmd_add(cfg)
    elif args.command == "list":
        cmd_list(cfg)
    elif args.command == "remove":
        cmd_remove(cfg, args.account_id)
    elif args.command == "login":
        cmd_login(cfg, args)
    elif args.command == "fetch":
        cmd_fetch(cfg, args)
    elif args.command == "read":
        cmd_read(cfg, args)
    elif args.command == "inspect":
        cmd_inspect(cfg, args)


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    main()
