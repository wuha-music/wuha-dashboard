#!/usr/bin/env python3
"""Entdeckt in Playlists – täglicher Playlist-Check für WUHA.

1. Liest Gmail-Mails mit dem Label "Playlist Sub" (IMAP, App-Passwort).
2. Sucht darin Playlisten (Spotify-Links, Soundcharts-Meldungen, DailyPlaylists/SubmitHub).
3. Prüft jede Playlist auf Spotify: Sind WUHA-Songs drin? Wie viele?
4. Schreibt data/playlists_found.json -> Dashboard-Bereich "Entdeckt in Playlists".

Regeln:
  * Neue Playlist kommt nur in die Liste, wenn WUHA wirklich drin ist.
  * Ist WUHA nicht mehr drin: 1 Tag grau ("entfernt"), am nächsten Tag gelöscht.
  * Kann nicht geprüft werden (kein Link, Spotify nicht erreichbar): bleibt wie sie ist.

Von Hand bearbeiten: data/imports/playlists_manuell.csv
  Spalten: Playlist, Link, Aktion
  Aktion leer/"hinzu"  -> Playlist (mit Link) prüfen und aufnehmen
  Aktion "ausblenden"  -> nie anzeigen
  Name + Link          -> Name einer Soundcharts-Meldung mit dem richtigen Link verbinden

Secrets (GitHub → Settings → Secrets → Actions):
  GMAIL_USER, GMAIL_APP_PASSWORD          Gmail lesen
  SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET Spotify-Suche + vollständige Prüfung
Ohne Spotify-Schlüssel wird die öffentliche Spotify-Einbettung genutzt (nur Playlisten mit Link).
"""
import base64
import csv
import datetime as dt
import email
import email.header
import email.utils
import html
import imaplib
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "playlists_found.json")
MANUAL = os.path.join(DATA, "imports", "playlists_manuell.csv")
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
ARTIST_ID = CFG.get("spotify_artist_id", "")
ARTIST_NAME = CFG.get("band", "WUHA")
LABEL = os.environ.get("GMAIL_LABEL", "Playlist Sub")
TZ = ZoneInfo("Europe/Vienna")
TODAY = dt.datetime.now(TZ).date().isoformat()
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130 Safari/537.36")
PL_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-z]{2}/)?(?:user/[^/]+/)?playlist/([A-Za-z0-9]{22})")
LOG = []


def log(msg):
    msg = str(msg)
    for k in ("GMAIL_APP_PASSWORD", "SPOTIFY_CLIENT_SECRET"):
        v = os.environ.get(k, "").strip()
        if len(v) > 4:
            msg = msg.replace(v, "[entfernt]")
    LOG.append(msg[:300])
    print(msg, file=sys.stderr)


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def http(url, data=None, headers=None, as_json=True):
    h = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8", "replace")
        final = r.geturl()
    return (json.loads(body) if as_json else body), final


# ------------------------------------------------------------------ Gmail

def _decode(s):
    out = ""
    for part, enc in email.header.decode_header(s or ""):
        out += part.decode(enc or "utf-8", "replace") if isinstance(part, bytes) else part
    return out


def _bodies(msg):
    text, htm = "", ""
    for p in msg.walk():
        if p.get_content_maintype() != "text" or p.get_filename():
            continue
        payload = p.get_payload(decode=True) or b""
        s = payload.decode(p.get_content_charset() or "utf-8", "replace")
        if p.get_content_subtype() == "html":
            htm += s
        else:
            text += s
    return text, htm


def _html_to_text(h):
    h = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", h)
    h = re.sub(r"(?i)<br\s*/?>|</(p|div|td|tr|li|h\d)>", "\n", h)
    return html.unescape(re.sub(r"<[^>]+>", " ", h))


def _anchors(h, text):
    out = []
    for href, label in re.findall(r'(?is)<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', h or ""):
        out.append((html.unescape(href), " ".join(html.unescape(re.sub(r"<[^>]+>", " ", label)).split())))
    for label, href in re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", text or ""):   # Markdown (SubmitHub)
        out.append((href, label.strip()))
    for label, href in re.findall(r"([^\n<>]{2,120}?)\s*<(https?://[^>\s]+)>", text or ""):   # Text <url>
        out.append((href, label.strip(" >\t")))
    return out


def parse_mail(sender, subject, text, htm):
    """-> Liste von Kandidaten {name, id, song, followers, source}"""
    found = []
    plain = text if text.strip() else _html_to_text(htm)
    src = ("Soundcharts" if "soundcharts" in sender else "DailyPlaylists" if "dailyplaylists" in (sender + plain) else
           "SubmitHub" if "submithub" in (sender + plain) else sender.split("@")[-1])

    # 1) Soundcharts: "New playlist entry" – nur Name, kein Link
    if "playlist entr" in plain.lower() and "Playlist name" in plain:
        lines = [ln.strip() for ln in plain.splitlines() if ln.strip()]
        for i, ln in enumerate(lines):
            if ln != "Playlist name":
                continue
            platform = lines[i - 1] if i >= 1 else ""
            song = lines[i - 2] if i >= 2 else ""
            j = i + 1
            while j < len(lines) and lines[j] in ("Position", "Followers", "Position Followers"):
                j += 1
            while j + 1 < len(lines) and re.match(r"^\d+\s*/\s*\d+$", lines[j + 1]):
                fol = lines[j + 2] if j + 2 < len(lines) else ""
                found.append({"name": lines[j], "id": None, "song": song, "platform": platform,
                              "followers_text": fol, "source": "Soundcharts"})
                j += 3

    # 2) direkte Spotify-Playlist-Links
    anchors = _anchors(htm, text)
    for href, label in anchors:
        m = PL_RE.search(href)
        if m:
            found.append({"name": label if label and "spotify.com" not in label else None, "id": m.group(1),
                          "song": None, "source": src})
    for m in PL_RE.finditer(plain):
        found.append({"name": None, "id": m.group(1), "song": None, "source": src})

    # 3) Tracking-Links (z. B. DailyPlaylists "Submission Approved") auflösen
    if re.search(r"approved|added|accepted|placed|featured", subject + " " + plain[:2000], re.I):
        skip = re.compile(r"facebook|instagram|website|dashboard|review|faq|submit|unsubscribe|trustpilot|privacy", re.I)
        tries = 0
        for href, label in anchors:
            if tries >= 8 or PL_RE.search(href) or not href.startswith("http") or skip.search(label or ""):
                continue
            if not re.search(r"/tr/cl/|click|track|redirect|link\.", href):
                continue
            tries += 1
            try:
                _, final = http(href, as_json=False)
            except Exception:  # noqa: BLE001
                continue
            m = PL_RE.search(final)
            if m:
                found.append({"name": label or None, "id": m.group(1), "song": None, "source": src})
    return found


def gmail_candidates(state):
    user, pw = os.environ.get("GMAIL_USER", "").strip(), os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not (user and pw):
        log("Gmail: GMAIL_USER / GMAIL_APP_PASSWORD fehlen – Mails werden nicht gelesen.")
        return []
    seen = set(state.get("mails_seen", []))
    out = []
    im = imaplib.IMAP4_SSL("imap.gmail.com")
    im.login(user, pw)
    allmail = None
    for line in im.list()[1]:
        s = line.decode("utf-8", "replace")
        if "\\All" in s:
            allmail = s.split(' "/" ')[-1].strip()
    im.select(allmail or "INBOX", readonly=True)
    query = "label:" + re.sub(r"[\s/]+", "-", LABEL.strip().lower())
    typ, data = im.search(None, "X-GM-RAW", '"' + query + '"')
    ids = data[0].split() if typ == "OK" and data and data[0] else []
    log(f"Gmail: {len(ids)} Mails mit Label „{LABEL}“, davon neu: …")
    new = 0
    for num in ids:
        typ, meta = im.fetch(num, "(X-GM-MSGID)")
        mid = re.search(rb"X-GM-MSGID (\d+)", meta[0]).group(1).decode() if typ == "OK" else num.decode()
        if mid in seen:
            continue
        typ, msgdata = im.fetch(num, "(RFC822)")
        if typ != "OK":
            continue
        msg = email.message_from_bytes(msgdata[0][1])
        sender, subject = _decode(msg.get("From", "")).lower(), _decode(msg.get("Subject", ""))
        text, htm = _bodies(msg)
        try:
            date = email.utils.parsedate_to_datetime(msg.get("Date")).astimezone(TZ).date().isoformat()
        except Exception:  # noqa: BLE001
            date = TODAY
        for c in parse_mail(sender, subject, text, htm):
            c["mail_date"] = date
            out.append(c)
        seen.add(mid)
        new += 1
    im.logout()
    LOG[-1] = LOG[-1].replace("…", str(new))
    state["mails_seen"] = sorted(seen)
    return out


# ---------------------------------------------------------------- Spotify

_TOKEN = {}


def sp_token():
    cid, sec = os.environ.get("SPOTIFY_CLIENT_ID", "").strip(), os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not (cid and sec):
        return None
    if "t" not in _TOKEN:
        auth = base64.b64encode(f"{cid}:{sec}".encode()).decode()
        try:
            j, _ = http("https://accounts.spotify.com/api/token", data=b"grant_type=client_credentials",
                        headers={"Authorization": "Basic " + auth, "Content-Type": "application/x-www-form-urlencoded"})
            _TOKEN["t"] = j["access_token"]
        except Exception as e:  # noqa: BLE001
            log(f"Spotify-Anmeldung fehlgeschlagen: {e}")
            _TOKEN["t"] = None
    return _TOKEN["t"]


def sp_api(path):
    return http("https://api.spotify.com/v1/" + path, headers={"Authorization": "Bearer " + sp_token()})[0]


def _is_wuha(artists):
    return any((a.get("id") == ARTIST_ID) or norm(a.get("name")) == norm(ARTIST_NAME) for a in artists or [])


def check_api(pid):
    meta = sp_api(f"playlists/{pid}?fields=name,followers.total,owner.display_name,external_urls")
    songs, total = [], 0
    url = None
    for ep in ("items", "tracks"):
        try:
            page = sp_api(f"playlists/{pid}/{ep}?limit=100")
            url = ep
            break
        except Exception as e:  # noqa: BLE001
            err = e
    if url is None:
        raise err
    pos = 0
    while page:
        for it in page.get("items", []):
            pos += 1
            tr = (it or {}).get("track") or (it or {}).get("item") or {}
            if _is_wuha(tr.get("artists")):
                songs.append({"title": tr.get("name"), "pos": pos})
        total = page.get("total", pos)
        nxt = page.get("next")
        page = http(nxt, headers={"Authorization": "Bearer " + sp_token()})[0] if nxt else None
    return {"name": meta.get("name"), "followers": (meta.get("followers") or {}).get("total"),
            "curator": (meta.get("owner") or {}).get("display_name"), "songs": songs, "total": total, "complete": True}


def _find_key(o, key):
    if isinstance(o, dict):
        if key in o:
            return o[key]
        for v in o.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(o, list):
        for v in o:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


def check_embed(pid):
    body, _ = http(f"https://open.spotify.com/embed/playlist/{pid}", as_json=False)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', body, re.S)
    if not m:
        raise RuntimeError("Einbettung ohne Daten")
    data = json.loads(m.group(1))
    tracks = _find_key(data, "trackList") or []
    entity = _find_key(data, "entity") or {}
    songs = [{"title": t.get("title"), "pos": i + 1} for i, t in enumerate(tracks)
             if re.search(r"(^|[,&\s])" + re.escape(ARTIST_NAME) + r"($|[,&\s])", t.get("subtitle") or "", re.I)]
    return {"name": entity.get("name") or entity.get("title"), "followers": None, "curator": entity.get("subtitle"),
            "songs": songs, "total": len(tracks), "complete": len(tracks) < 100}


def check(pid):
    """-> dict mit songs/complete oder Exception"""
    if sp_token():
        try:
            return check_api(pid)
        except Exception as e:  # noqa: BLE001
            log(f"Spotify-API {pid}: {e} – versuche Einbettung")
    return check_embed(pid)


def search_playlist(name, song=None):
    """Spotify-ID zu einem Playlist-Namen (Soundcharts) finden."""
    if not sp_token():
        return None
    q = urllib.parse.quote(name[:100])
    res = sp_api(f"search?type=playlist&limit=10&q={q}")
    items = [i for i in ((res.get("playlists") or {}).get("items") or []) if i]
    exact = [i for i in items if norm(i.get("name")) == norm(name)] or \
            [i for i in items if norm(name) in norm(i.get("name")) or norm(i.get("name")) in norm(name)]
    for cand in exact[:5]:
        try:
            r = check(cand["id"])
        except Exception:  # noqa: BLE001
            continue
        if r["songs"] and (not song or any(norm(song.split(" - ")[0]) in norm(s["title"]) for s in r["songs"])):
            return cand["id"]
    if len(exact) == 1:
        return exact[0]["id"]
    return None


# ------------------------------------------------------------------ Ablauf

def load_manual():
    rows = []
    if os.path.exists(MANUAL):
        with open(MANUAL, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
                if r.get("playlist") or r.get("link"):
                    rows.append(r)
    return rows


def main():
    os.makedirs(DATA, exist_ok=True)
    state = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    state.setdefault("playlists", {})      # id -> Eintrag (nur verifizierte)
    state.setdefault("pending", {})        # norm(name) -> Kandidat ohne Link
    state.setdefault("removed", [])
    pls, pending = state["playlists"], state["pending"]

    manual = load_manual()
    hidden = {norm(r.get("playlist")) for r in manual if r.get("aktion", "").lower().startswith("ausblend")}
    hidden |= {m.group(1) for r in manual if r.get("aktion", "").lower().startswith("ausblend")
               for m in [PL_RE.search(r.get("link", ""))] if m}
    name_links = {norm(r["playlist"]): PL_RE.search(r["link"]).group(1)
                  for r in manual if r.get("playlist") and PL_RE.search(r.get("link", ""))}

    # Kandidaten sammeln: Mails + Startliste + manuelle Liste
    cands = []
    try:
        cands += gmail_candidates(state)
    except Exception as e:  # noqa: BLE001
        log(f"Gmail: Fehler – {e}")
    cands += state.pop("seed", [])
    for r in manual:
        m = PL_RE.search(r.get("link", ""))
        if m and not r.get("aktion", "").lower().startswith("ausblend"):
            cands.append({"name": r.get("playlist") or None, "id": m.group(1), "source": "manuell"})

    for c in cands:
        if c.get("platform") and c["platform"].lower() != "spotify":
            continue
        key = norm(c.get("name"))
        pid = c.get("id") or name_links.get(key)
        if not pid:
            p = pending.setdefault(key, {"name": c["name"], "songs": [], "source": c.get("source"),
                                         "first_seen": c.get("mail_date", TODAY), "followers_text": c.get("followers_text")})
            if c.get("song") and c["song"] not in p["songs"]:
                p["songs"].append(c["song"])
            continue
        e = pls.get(pid) or {"id": pid, "first_seen": c.get("mail_date", TODAY), "verified": False}
        e.setdefault("names", [])
        if c.get("name") and c["name"] not in e["names"]:
            e["names"].append(c["name"])
        e["source"] = e.get("source") or c.get("source")
        e["first_seen"] = min(e["first_seen"], c.get("mail_date", TODAY))
        pls[pid] = e

    # Namen ohne Link auflösen (manuelle Zuordnung oder Spotify-Suche)
    known = {norm(n): pid for pid, e in pls.items() for n in (e.get("names") or []) + [e.get("name")] if n}
    for key, p in list(pending.items()):
        pid = name_links.get(key) or known.get(key)
        if not pid:
            try:
                pid = search_playlist(p["name"], (p.get("songs") or [None])[0])
            except Exception as e:  # noqa: BLE001
                log(f"Suche „{p['name']}“: {e}")
        if pid:
            e = pls.get(pid) or {"id": pid, "first_seen": p["first_seen"], "verified": False, "names": []}
            if p["name"] not in e["names"]:
                e["names"].append(p["name"])
            e["source"] = e.get("source") or p.get("source")
            e["first_seen"] = min(e["first_seen"], p["first_seen"])
            e.setdefault("followers_text", p.get("followers_text"))
            pls[pid] = e
            del pending[key]

    # Prüfen
    for pid, e in list(pls.items()):
        if pid in hidden or norm((e.get("names") or [""])[0]) in hidden:
            del pls[pid]
            continue
        try:
            r = check(pid)
        except Exception as ex:  # noqa: BLE001
            log(f"Prüfung {pid}: {ex}")
            e["checked_ok"] = False
            if not e.get("verified"):
                e.setdefault("tries", 0)
                e["tries"] += 1
                if e["tries"] > 7:
                    del pls[pid]
            continue
        e.update({"name": r["name"] or (e.get("names") or ["?"])[0], "curator": r.get("curator"),
                  "followers": r.get("followers") or e.get("followers"), "total_tracks": r["total"],
                  "url": f"https://open.spotify.com/playlist/{pid}", "last_check": TODAY, "checked_ok": True})
        if r["songs"]:
            e.update({"songs": r["songs"], "count": len(r["songs"]), "status": "aktiv",
                      "last_seen": TODAY, "missing_since": None, "verified": True})
        elif not r["complete"]:
            e["checked_ok"] = False     # nur Teil der Playlist sichtbar – nichts ändern
            log(f"{e['name']}: nur {r['total']} Titel sichtbar, WUHA nicht dabei – Spotify-Schlüssel nötig.")
        elif not e.get("verified"):
            del pls[pid]                # nie drin gewesen -> nicht aufnehmen
        elif not e.get("missing_since"):
            e.update({"missing_since": TODAY, "status": "entfernt", "count": 0})
        elif e["missing_since"] < TODAY:
            state["removed"].insert(0, {"name": e["name"], "url": e["url"], "removed": TODAY,
                                        "songs": [s["title"] for s in e.get("songs", [])]})
            del pls[pid]

    state["removed"] = state["removed"][:50]
    active = [e for e in pls.values() if e.get("verified") and e.get("status") == "aktiv"]
    state["summary"] = {"playlists": len(active), "songs": sum(e.get("count", 0) for e in active),
                        "followers": sum(e.get("followers") or 0 for e in active)}
    state["updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    state["spotify_api"] = bool(sp_token())
    state["log"] = LOG
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(json.dumps(state["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
