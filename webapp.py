# -*- coding: utf-8 -*-
"""
webapp.py — my-mail 의 로컬 웹 콘솔.

터미널 대신 브라우저에서 계정 관리 / 메일 목록 / 본문 읽기를 할 수 있다.

  python webapp.py            → http://127.0.0.1:8765 열기
  python webapp.py --port 9000

구조 메모:
  Playwright 동기 API 는 만든 스레드에서만 쓸 수 있어서, Flask 를 threaded=False 로
  띄워 모든 요청을 한 스레드에서 순차 처리한다. 덕분에 계정별 브라우저 컨텍스트를
  캐시해두고 재사용할 수 있다(매 요청마다 브라우저를 새로 띄우면 매우 느리다).
"""
import argparse
import json
import threading
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from playwright.sync_api import sync_playwright

import mymail as M
import archive as AR

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"

app = Flask(__name__, static_folder=None)


# ---------------------------------------------------------------- 브라우저 풀

class BrowserPool:
    """계정별 persistent context 를 열어두고 재사용한다."""

    def __init__(self):
        self.pw = None
        self.ctxs = {}          # account_id -> (context, page, headed)

    def _ensure_pw(self):
        if self.pw is None:
            self.pw = sync_playwright().start()
        return self.pw

    def page(self, cfg, account, headed=False):
        key = account["id"]
        cached = self.ctxs.get(key)
        if cached:
            ctx, page, was_headed = cached
            if was_headed == headed:
                try:
                    page.title()            # 살아있는지 확인
                    return page
                except Exception:
                    pass
            self.close(key)

        ctx = M.open_context(self._ensure_pw(), cfg, account, headed=headed)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        self.ctxs[key] = (ctx, page, headed)
        return page

    def close(self, key):
        cached = self.ctxs.pop(key, None)
        if cached:
            try:
                cached[0].close()
            except Exception:
                pass

    def close_all(self):
        for key in list(self.ctxs):
            self.close(key)
        if self.pw is not None:
            try:
                self.pw.stop()
            except Exception:
                pass
            self.pw = None


pool = BrowserPool()


def cfg():
    return M.load_config()


def get_account(aid):
    return M.find_account(M.load_accounts(), aid)


# ---------------------------------------------------------------- 정적 파일

@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/app.js")
def appjs():
    return send_from_directory(WEB_DIR, "app.js")


@app.get("/style.css")
def stylecss():
    return send_from_directory(WEB_DIR, "style.css")


# ---------------------------------------------------------------- 계정 API

# 서버가 켜진 뒤 전체 수집을 한 번 했는지. (페이지를 새로고침할 때마다
# 몇 분짜리 수집이 다시 도는 것을 막는다)
_synced_this_boot = False


@app.get("/api/config")
def api_config():
    c = cfg()
    return jsonify({
        "syncOnStart": bool(c.get("sync_on_start", True)),
        "syncedThisBoot": _synced_this_boot,
        "syncLimit": int(c.get("sync_limit", 50)),
    })


@app.post("/api/sync/folder")
def api_sync_folder():
    """편지함 하나를 수집한다: 목록 조회 → 안읽음 집계 → 없는 메일 보관."""
    data = request.get_json(force=True) or {}
    acct = get_account(data.get("account", ""))
    if not acct:
        return jsonify({"error": "계정을 찾을 수 없습니다."}), 404

    c = cfg()
    provider = M.get_provider(c, acct["provider"])
    page = pool.page(c, acct)
    if not M.ensure_logged_in(page, provider, acct):
        return jsonify({"error": "로그인이 필요합니다.", "needLogin": True}), 401

    err, folder = _goto_folder_if_needed(page, provider, data.get("folder"))
    if err:
        return jsonify({"error": err}), 400

    fname = folder["name"] if folder else "받은 편지함"
    limit = int(data.get("limit") or c.get("sync_limit", 50))
    mails, mode = M.fetch_mails(page, provider, limit)

    unread = sum(1 for m in mails if m.get("unread"))
    if mode != "heuristic":
        AR.save_folder_count(acct["id"], fname, unread, len(mails))

    res = {"folder": fname, "unread": unread, "total": len(mails), "mode": mode,
           "new": 0, "bodies": 0, "skipped_unread": 0}
    if mails:
        res.update(M.archive_mails(page, provider, acct, fname, mails,
                                   include_unread=bool(data.get("includeUnread"))))
    res["accountUnread"] = AR.total_unread(acct["id"])
    res["archived"] = AR.stats(acct["id"])["total"]
    return jsonify(res)


@app.post("/api/sync/done")
def api_sync_done():
    global _synced_this_boot
    _synced_this_boot = True
    return jsonify({"ok": True})


@app.get("/api/accounts")
def api_accounts():
    c = cfg()
    out = []
    for a in M.load_accounts():
        profile = BASE_DIR / c.get("user_data_dir", "user_data") / a["id"] / "Default"
        out.append({
            "id": a["id"],
            "email": a["email"],
            "provider": a["provider"],
            "session": profile.exists(),
            "unread": AR.total_unread(a["id"]),          # 캐시된 값 (없으면 null)
            "archived": AR.stats(a["id"])["total"],
        })
    return jsonify({"accounts": out, "providers": list(c.get("providers", {}).keys())})


@app.post("/api/accounts")
def api_add_account():
    data = request.get_json(force=True)
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    provider = (data.get("provider") or "").strip()

    if "@" not in email:
        return jsonify({"error": "올바른 이메일 주소를 입력하세요."}), 400
    if not password:
        return jsonify({"error": "비밀번호를 입력하세요."}), 400

    accounts = M.load_accounts()
    if M.find_account(accounts, email):
        return jsonify({"error": "이미 등록된 이메일입니다."}), 400

    providers = cfg().get("providers", {})
    if provider not in providers:
        return jsonify({"error": f"알 수 없는 provider: {provider}"}), 400

    aid = M.make_account_id(email, accounts)
    accounts.append({"id": aid, "email": email,
                     "provider": provider, "password": password})
    M.save_accounts(accounts)
    return jsonify({"id": aid})


@app.delete("/api/accounts/<aid>")
def api_remove_account(aid):
    import shutil
    accounts = M.load_accounts()
    acct = M.find_account(accounts, aid)
    if not acct:
        return jsonify({"error": "계정을 찾을 수 없습니다."}), 404

    pool.close(acct["id"])
    shutil.rmtree(BASE_DIR / cfg().get("user_data_dir", "user_data") / acct["id"],
                  ignore_errors=True)
    M.seen_path(acct).unlink(missing_ok=True)
    M.save_accounts([a for a in accounts if a["id"] != acct["id"]])
    return jsonify({"ok": True})


# ---------------------------------------------------------------- 메일 API

@app.get("/api/folders")
def api_folders():
    aid = request.args.get("account", "")
    acct = get_account(aid)
    if not acct:
        return jsonify({"error": "계정을 찾을 수 없습니다."}), 404

    c = cfg()
    provider = M.get_provider(c, acct["provider"])
    page = pool.page(c, acct)
    if not M.ensure_logged_in(page, provider, acct):
        return jsonify({"error": "로그인이 필요합니다.", "needLogin": True}), 401

    folders = M.list_folders(page, provider)
    cached = (AR.load_counts(acct["id"]).get("folders") or {})
    st = AR.stats(acct["id"])["folders"]
    for f in folders:                       # 캐시된 안읽음/보관 개수를 붙여준다
        c = cached.get(f["name"]) or {}
        f["unread"] = c.get("unread")
        f["total"] = c.get("total")
        f["archived"] = (st.get(AR.slug(f["name"])) or {}).get("count", 0)
    return jsonify({"folders": folders})


@app.get("/api/unread")
def api_unread():
    """편지함 하나의 안읽음 개수를 센다. (UI 가 폴더별로 하나씩 호출)"""
    aid = request.args.get("account", "")
    acct = get_account(aid)
    if not acct:
        return jsonify({"error": "계정을 찾을 수 없습니다."}), 404

    c = cfg()
    provider = M.get_provider(c, acct["provider"])
    page = pool.page(c, acct)
    if not M.ensure_logged_in(page, provider, acct):
        return jsonify({"error": "로그인이 필요합니다.", "needLogin": True}), 401

    name = request.args.get("folder") or ""
    err, folder = _goto_folder_if_needed(page, provider, name)
    if err:
        return jsonify({"error": err}), 400

    fname = folder["name"] if folder else "받은 편지함"
    unread, total, _, _ = M.count_unread(page, provider, acct, fname)
    return jsonify({"folder": fname, "unread": unread, "total": total,
                    "accountUnread": AR.total_unread(acct["id"])})


@app.post("/api/archive")
def api_archive():
    """현재 편지함에서 아직 저장 안 된 메일을 로컬로 복사한다."""
    data = request.get_json(force=True) or {}
    acct = get_account(data.get("account", ""))
    if not acct:
        return jsonify({"error": "계정을 찾을 수 없습니다."}), 404

    c = cfg()
    provider = M.get_provider(c, acct["provider"])
    page = pool.page(c, acct)
    if not M.ensure_logged_in(page, provider, acct):
        return jsonify({"error": "로그인이 필요합니다.", "needLogin": True}), 401

    err, folder = _goto_folder_if_needed(page, provider, data.get("folder"))
    if err:
        return jsonify({"error": err}), 400

    fname = folder["name"] if folder else "받은 편지함"
    mails, mode = M.fetch_mails(page, provider, int(data.get("limit") or 200))
    if not mails:
        return jsonify({"new": 0, "bodies": 0, "skipped_unread": 0,
                        "archived": AR.stats(acct["id"])["total"]})

    res = M.archive_mails(page, provider, acct, fname, mails,
                          include_unread=bool(data.get("includeUnread")))
    res["archived"] = AR.stats(acct["id"])["total"]
    res["folder"] = fname
    return jsonify(res)


def _goto_folder_if_needed(page, provider, folder_key):
    """folder_key 로 폴더 이동. (오류메시지, 폴더dict) 를 돌려준다."""
    if not folder_key:
        return None, None
    folders = M.list_folders(page, provider)
    if not folders:
        return None, None                       # 폴더를 지원하지 않는 서비스
    f = M.resolve_folder(folders, folder_key)
    if not f:
        names = ", ".join(x["name"] for x in folders)
        return f"'{folder_key}' 폴더를 찾을 수 없습니다. (사용 가능: {names})", None
    # 이미 그 폴더를 보고 있으면 다시 이동하지 않는다
    if f["id"] not in page.url:
        M.goto_folder(page, provider, f)
    return None, f


@app.get("/api/mails/cached")
def api_mails_cached():
    """보관본만으로 목록을 즉시 돌려준다 (브라우저 접속 없음 → 바로 표시)."""
    acct = get_account(request.args.get("account", ""))
    if not acct:
        return jsonify({"error": "계정을 찾을 수 없습니다."}), 404

    fname = request.args.get("folder") or "받은 편지함"
    recs = AR.list_folder(acct["id"], fname)
    mails = [{
        "n": i,
        "key": r.get("key"),
        "sender": r.get("sender", ""),
        "subject": r.get("subject", ""),
        "date": r.get("date", ""),
        "unread": bool(r.get("unread")),
        "isNew": False,
        "hasBody": bool(r.get("body_saved")),
    } for i, r in enumerate(recs, 1)]
    return jsonify({"mails": mails, "mode": "archive", "folder": fname,
                    "email": acct["email"]})


@app.get("/api/mails")
def api_mails():
    aid = request.args.get("account", "")
    acct = get_account(aid)
    if not acct:
        return jsonify({"error": "계정을 찾을 수 없습니다."}), 404

    c = cfg()
    provider = M.get_provider(c, acct["provider"])
    page = pool.page(c, acct)

    if not M.ensure_logged_in(page, provider, acct):
        return jsonify({"error": "로그인이 필요합니다.",
                        "needLogin": True, "url": page.url}), 401

    err, folder = _goto_folder_if_needed(page, provider, request.args.get("folder"))
    if err:
        return jsonify({"error": err}), 400

    limit = int(request.args.get("limit") or c.get("limit", 20))
    mails, mode = M.fetch_mails(page, provider, limit)

    seen = M.load_seen(acct)
    for i, m in enumerate(mails, 1):
        m["n"] = i
        m["key"] = M.mail_key(m)
        m["isNew"] = m["key"] not in seen
    M.save_seen(acct, seen | {M.mail_key(m) for m in mails})

    # 지금 본 편지함의 안읽음 개수는 공짜로 알 수 있으니 캐시에 남긴다
    if mode != "heuristic":
        M.AR.save_folder_count(acct["id"], folder["name"] if folder else "받은 편지함",
                               sum(1 for m in mails if m.get("unread")), len(mails))

    return jsonify({"mails": mails, "mode": mode, "email": acct["email"],
                    "folder": folder["name"] if folder else None})


@app.get("/api/mail")
def api_mail():
    aid = request.args.get("account", "")
    n = int(request.args.get("n") or 0)
    acct = get_account(aid)
    if not acct:
        return jsonify({"error": "계정을 찾을 수 없습니다."}), 404

    # 이미 로컬에 본문이 있으면 브라우저를 건드리지 않고 바로 돌려준다.
    # (fresh=1 이면 강제로 원본을 다시 가져온다)
    key = request.args.get("key") or ""
    fname0 = request.args.get("folder") or "받은 편지함"
    if key and request.args.get("fresh") != "1":
        rec = AR.load_mail(acct["id"], fname0, key)
        if rec and rec.get("body_saved"):
            return jsonify({
                "subject": rec.get("subject_full") or rec.get("subject", ""),
                "header": rec.get("header", ""),
                "body": rec.get("body", ""),
                "source": "archive",
                "savedAt": rec.get("updated") or rec.get("first_seen"),
            })

    c = cfg()
    provider = M.get_provider(c, acct["provider"])
    if not provider.get("row"):
        return jsonify({"error": f"{acct['provider']} 는 목록 셀렉터(row)가 없어 "
                                 f"본문 열기를 지원하지 않습니다."}), 400

    page = pool.page(c, acct)
    err, _ = _goto_folder_if_needed(page, provider, request.args.get("folder"))
    if err:
        return jsonify({"error": err}), 400

    rows = M.open_mail_rows(page, provider)
    if not rows:
        if not M.ensure_logged_in(page, provider, acct):
            return jsonify({"error": "로그인이 필요합니다.", "needLogin": True}), 401
        _goto_folder_if_needed(page, provider, request.args.get("folder"))
        rows = M.open_mail_rows(page, provider)
    if not rows:
        return jsonify({"error": "메일 목록을 찾지 못했습니다."}), 404
    if not (1 <= n <= len(rows)):
        return jsonify({"error": f"{n} 번 메일이 없습니다. (1~{len(rows)})"}), 400

    # 클릭하면 목록 핸들이 갈릴 수 있으니 메타데이터를 먼저 확보
    meta, _ = M.fetch_mails(page, provider, max(n, 20))
    rows[n - 1].click()
    subject, header, body = M.read_opened_mail(page, provider)
    if not (subject or header or body):
        return jsonify({"error": "본문을 찾지 못했습니다. "
                                 "config.json 의 view.body 셀렉터를 확인하세요."}), 404

    # 방금 열어서 읽음 처리됐으니 본문도 보관해 둔다 → 다음부터는 즉시 열린다
    fname = folder["name"] if folder else "받은 편지함"
    if body and n - 1 < len(meta):
        m = meta[n - 1]
        AR.save_mail(acct["id"], acct.get("email", ""), fname, m, M.mail_key(m),
                     body=body, header=header, subject_full=subject, seq=n)

    return jsonify({"subject": subject, "header": header, "body": body,
                    "source": "live"})


# ---------------------------------------------------------------- 로그인 API

@app.post("/api/login")
def api_login():
    aid = (request.get_json(force=True) or {}).get("account", "")
    acct = get_account(aid)
    if not acct:
        return jsonify({"error": "계정을 찾을 수 없습니다."}), 404

    c = cfg()
    provider = M.get_provider(c, acct["provider"])
    pool.close(acct["id"])                     # headed 창으로 새로 연다
    page = pool.page(c, acct, headed=True)

    page.goto(provider["mail_url"], wait_until="domcontentloaded")
    M.wait_for_settle(page)
    if M.is_logged_in(page, provider):
        return jsonify({"status": "ok"})

    if not M.is_login_page(page, provider) and provider.get("login_url"):
        page.goto(provider["login_url"], wait_until="domcontentloaded")
        M.wait_for_settle(page)

    M.try_auto_login(page, provider, acct)     # 아이디/비번 미리 채움
    if M.is_logged_in(page, provider):
        return jsonify({"status": "ok"})
    return jsonify({"status": "manual", "url": page.url})


@app.post("/api/login/verify")
def api_login_verify():
    aid = (request.get_json(force=True) or {}).get("account", "")
    acct = get_account(aid)
    if not acct:
        return jsonify({"error": "계정을 찾을 수 없습니다."}), 404

    c = cfg()
    provider = M.get_provider(c, acct["provider"])
    page = pool.page(c, acct, headed=True)
    page.goto(provider["mail_url"], wait_until="domcontentloaded")
    M.wait_for_settle(page)

    ok = M.is_logged_in(page, provider)
    if ok:
        pool.close(acct["id"])                 # 이후 조회는 headless 로
    return jsonify({"status": "ok" if ok else "fail", "url": page.url})


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="my-mail 웹 콘솔")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="브라우저 자동 실행 안 함")
    args = ap.parse_args()

    url = f"http://127.0.0.1:{args.port}"
    print(f"\n  my-mail 웹 콘솔 → {url}")
    print("  종료하려면 이 창에서 Ctrl+C\n")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        # 외부에 노출하지 않도록 127.0.0.1 에만 바인딩.
        # Playwright 동기 API 때문에 threaded=False 필수.
        app.run(host="127.0.0.1", port=args.port,
                threaded=False, use_reloader=False)
    finally:
        pool.close_all()


if __name__ == "__main__":
    main()
