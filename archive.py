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

# 메일 목록의 날짜 문자열 → 정렬용 시각.
# Zoho(한국어)는 연도를 안 준다:  '토 8월 22 7:56 오후'  /  오늘 메일은 '10:04 오전'
_WEEKDAYS = "월화수목금토일"
_DATE_RE = re.compile(
    r"(?:([월화수목금토일])\s+)?"          # 요일 (있을 때만)
    r"(?:(\d{1,2})월\s*(\d{1,2})\s+)?"     # M월 D (오늘 메일은 없음)
    r"(\d{1,2}):(\d{2})\s*(오전|오후)"
)


def parse_mail_date(text, now=None):
    """목록의 날짜 문자열을 datetime 으로. 못 읽으면 None.

    연도가 없으므로 올해/작년 중 **요일이 맞는 쪽**을 고르고,
    그래도 못 정하면 미래가 아닌 쪽을 쓴다.
    """
    if not text:
        return None
    m = _DATE_RE.search(text.strip())
    if not m:
        return None
    wd, mon, day, hh, mm, ampm = m.groups()
    hh, mm = int(hh), int(mm)
    if ampm == "오전":
        hh = 0 if hh == 12 else hh
    else:
        hh = 12 if hh == 12 else hh + 12

    now = now or datetime.now()
    if not (mon and day):                      # 시각만 있으면 오늘
        return now.replace(hour=hh, minute=mm, second=0, microsecond=0)

    mon, day = int(mon), int(day)
    cands = []
    for year in (now.year, now.year - 1):
        try:
            cands.append(datetime(year, mon, day, hh, mm))
        except ValueError:                     # 2월 29일 같은 경우
            pass
    if not cands:
        return None
    if wd:                                     # 요일이 맞는 해를 우선
        want = _WEEKDAYS.index(wd)
        for c in cands:
            if c.weekday() == want:
                return c
    for c in cands:                            # 아니면 미래가 아닌 쪽
        if c <= now:
            return c
    return cands[-1]


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
              body=None, header=None, subject_full=None, seq=None):
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
    # 목록에서의 위치(1부터). 보관본만으로 목록을 재현할 때 정렬 기준이 된다.
    if seq is None:
        seq = mail.get("n")
    if seq is not None:
        rec["seq"] = int(seq)
    if body is not None:
        rec["body"] = body
        rec["body_saved"] = bool(body)
        if header:
            rec["header"] = header
        if subject_full:
            rec["subject_full"] = subject_full
    rec.setdefault("body_saved", False)

    # 정렬용 시각을 저장 시점에 한 번만 계산해 둔다
    dt = parse_mail_date(rec.get("date"))
    if dt:
        rec["date_iso"] = dt.isoformat(timespec="seconds")

    p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec


def list_folder(account_id, folder_name, limit=200):
    """보관본만으로 편지함 목록을 재현한다 (브라우저 접속 없음)."""
    d = folder_dir(account_id, folder_name)
    if not d.exists():
        return []
    recs = []
    for f in d.glob("*.json"):
        try:
            recs.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    # 저장 당시의 목록 순서(seq)를 우선 쓰고, 없으면 최근 저장 순
    recs.sort(key=lambda r: (r.get("seq") is None, r.get("seq") or 0,
                             r.get("updated") or ""))
    return recs[:limit]


def _sort_key(rec):
    """최신이 위로. 날짜를 못 읽은 메일은 보관 시각으로 대신한다."""
    iso = rec.get("date_iso")
    if not iso:
        dt = parse_mail_date(rec.get("date"))
        iso = dt.isoformat(timespec="seconds") if dt else ""
    return (iso or "", rec.get("first_seen") or "")


def list_account(account_id, limit=500):
    """계정의 모든 편지함 메일을 한 목록으로 (최신순). 브라우저 접속 없음."""
    root = account_dir(account_id)
    if not root.exists():
        return []
    recs = []
    for fdir in root.iterdir():
        if not fdir.is_dir():
            continue
        for f in fdir.glob("*.json"):
            try:
                recs.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
    recs.sort(key=_sort_key, reverse=True)
    return recs[:limit]


def find_mail(account_id, key):
    """편지함을 몰라도 키로 메일을 찾는다."""
    root = account_dir(account_id)
    if not root.exists():
        return None
    for fdir in root.iterdir():
        if fdir.is_dir():
            p = fdir / f"{key}.json"
            if p.exists():
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    return None
    return None


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
