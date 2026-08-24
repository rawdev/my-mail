# -*- coding: utf-8 -*-
"""
archive.py — 메일을 로컬에 복사해 보관하고, 편지함별 안읽음 개수를 집계한다.

저장 구조:
    archive/<계정ID>/<편지함>/<메일키>.json     메일 한 통
    archive/<계정ID>/_counts.json               편지함별 안읽음/전체 개수 캐시

메일키는 (보낸사람 + 제목 + 날짜) 해시라서, 이미 저장한 메일은 다시 저장하지 않는다.

[중요] 본문을 가져오려면 메일을 '열어야' 하고, 열면 서버에서 읽음 처리된다.
그래서 기본값은 **안읽은 메일의 본문은 건너뛴다**. 안읽음 개수를 망가뜨리지 않기
위해서다. 안읽은 메일도 메타데이터(보낸사람/제목/날짜)는 바로 저장되고, 본문은
나중에 그 메일을 읽은 뒤에 채워진다. include_unread=True 로 강제할 수 있다.
"""
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ARCHIVE_DIR = BASE_DIR / "archive"

_SLUG_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def slug(name):
    """편지함 이름을 파일시스템에 안전한 폴더명으로."""
    s = _SLUG_BAD.sub("_", (name or "기본").strip())
    s = s.strip(". ")                      # 윈도우는 끝의 점/공백을 싫어한다
    return (s or "기본")[:60]


def account_dir(account_id):
    return ARCHIVE_DIR / slug(account_id)


def folder_dir(account_id, folder_name):
    return account_dir(account_id) / slug(folder_name)


def mail_path(account_id, folder_name, key):
    return folder_dir(account_id, folder_name) / f"{key}.json"


def load_mail(account_id, folder_name, key):
    p = mail_path(account_id, folder_name, key)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def has_body(account_id, folder_name, key):
    rec = load_mail(account_id, folder_name, key)
    return bool(rec and rec.get("body_saved"))


def save_mail(account_id, email, folder_name, mail, key,
              body=None, header=None, subject_full=None):
    """메일 한 통을 저장(또는 본문만 나중에 채워넣기). 저장된 레코드를 반환."""
    d = folder_dir(account_id, folder_name)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{key}.json"

    rec = load_mail(account_id, folder_name, key) or {
        "key": key,
        "account": account_id,
        "email": email,
        "folder": folder_name,
        "first_seen": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    rec.update({
        "sender": mail.get("sender", ""),
        "subject": mail.get("subject", ""),
        "date": mail.get("date", ""),
        "unread": bool(mail.get("unread")),
        "updated": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    })
    if body is not None:
        rec["body"] = body
        rec["body_saved"] = bool(body)
        if header:
            rec["header"] = header
        if subject_full:
            rec["subject_full"] = subject_full
    rec.setdefault("body_saved", False)

    p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec


def stats(account_id):
    """보관된 메일 수를 편지함별로 센다."""
    root = account_dir(account_id)
    if not root.exists():
        return {"total": 0, "with_body": 0, "folders": {}}
    total = with_body = 0
    folders = {}
    for fdir in sorted(root.iterdir()):
        if not fdir.is_dir():
            continue
        n = nb = 0
        for f in fdir.glob("*.json"):
            n += 1
            try:
                if json.loads(f.read_text(encoding="utf-8")).get("body_saved"):
                    nb += 1
            except Exception:
                pass
        folders[fdir.name] = {"count": n, "with_body": nb}
        total += n
        with_body += nb
    return {"total": total, "with_body": with_body, "folders": folders}


# ---------------------------------------------------------------- 안읽음 개수 캐시

def counts_path(account_id):
    return account_dir(account_id) / "_counts.json"


def load_counts(account_id):
    p = counts_path(account_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"folders": {}, "updated": None}


def save_folder_count(account_id, folder_name, unread, total):
    data = load_counts(account_id)
    data.setdefault("folders", {})[folder_name] = {
        "unread": unread,
        "total": total,
        "checked": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    p = counts_path(account_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def total_unread(account_id):
    """계정 전체 안읽음 = 캐시된 편지함 개수의 합. (없으면 None)"""
    folders = load_counts(account_id).get("folders") or {}
    if not folders:
        return None
    return sum(int(v.get("unread") or 0) for v in folders.values())
