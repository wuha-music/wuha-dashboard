#!/usr/bin/env python3
"""Sammelt täglich die Zahlen von WUHA und speichert sie in data/history.json.

Läuft ohne Zusatzpakete (nur Python-Standardbibliothek).
Zugangsdaten kommen aus Umgebungsvariablen (GitHub Secrets):
  META_PAGE_TOKEN        Facebook-Seiten-Token (deckt Facebook + Instagram ab)
  YOUTUBE_API_KEY        Google API-Schlüssel (YouTube Data API v3)
  TIKTOK_CLIENT_KEY      TikTok-App
  TIKTOK_CLIENT_SECRET
  TIKTOK_REFRESH_TOKEN
Fehlt etwas, wird die Plattform übersprungen bzw. auf öffentliche Zahlen ausgewichen.
"""
import csv
import datetime as dt
import glob
import json
import os
import re
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
HISTORY = os.path.join(DATA, "history.json")
UA = "Mozilla/5.0 (compatible; wuha-dashboard/1.0)"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130 Safari/537.36")

CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
LOG = []


def log(msg):
    LOG.append(msg)
    print(msg, file=sys.stderr)


def http(url, data=None, headers=None, ua=UA, as_json=True):
    h = {"User-Agent": ua, "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"}
    h.update(headers or {})
    if isinstance(data, dict):
        data = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body) if as_json else body


def num(s):
    """'1.234' / '1,2K' / '3 Mio.' -> int"""
    s = str(s).strip().replace(" ", " ")
    m = re.match(r"([\d.,]+)\s*([KkMm])?", s)
    if not m:
        return None
    n, suf = m.group(1), (m.group(2) or "").lower()
    if suf:
        return int(float(n.replace(",", ".")) * (1000 if suf == "k" else 1_000_000))
    return int(re.sub(r"[.,]", "", n))


# ---------------------------------------------------------------- Plattformen

def meta():
    """Facebook-Seite + verknüpftes Instagram-Konto über einen Seiten-Token."""
    token = os.environ.get("META_PAGE_TOKEN")
    if not token:
        log("Meta: kein META_PAGE_TOKEN gesetzt - übersprungen")
        return {}
    v = CFG.get("graph_api_version", "v25.0")
    base = f"https://graph.facebook.com/{v}"
    page_id = CFG.get("facebook_page_id") or "me"
    out = {}
    q = urllib.parse.urlencode({
        "fields": "id,name,followers_count,fan_count,"
                  "instagram_business_account{id,username,followers_count,media_count}",
        "access_token": token})
    page = http(f"{base}/{page_id}?{q}")
    fb = {"followers": page.get("followers_count"), "likes": page.get("fan_count")}
    ig_acc = page.get("instagram_business_account") or {}

    yesterday = dt.date.today() - dt.timedelta(days=1)
    since = yesterday.isoformat()
    until = dt.date.today().isoformat()

    # Facebook: tägliche Aufrufe/Reichweite. Meta benennt Metriken öfter um,
    # deshalb wird jede einzeln versucht.
    for key, metric in [("views", "page_media_view"),
                        ("reach", "page_total_media_view_unique"),
                        ("reach", "page_impressions_unique")]:
        if key in fb:
            continue
        try:
            r = http(f"{base}/{page['id']}/insights?" + urllib.parse.urlencode({
                "metric": metric, "period": "day", "since": since, "until": until,
                "access_token": token}))
            vals = r["data"][0]["values"]
            fb[key] = vals[-1]["value"]
        except Exception as e:  # noqa: BLE001
            log(f"Facebook: Metrik {metric} nicht verfügbar ({e})")
    out["facebook"] = fb

    if ig_acc:
        ig = {"followers": ig_acc.get("followers_count"), "posts": ig_acc.get("media_count")}
        for key, metric in [("reach", "reach"), ("views", "views"),
                            ("profile_views", "profile_views")]:
            try:
                r = http(f"{base}/{ig_acc['id']}/insights?" + urllib.parse.urlencode({
                    "metric": metric, "period": "day", "metric_type": "total_value",
                    "since": since, "until": until, "access_token": token}))
                ig[key] = r["data"][0]["total_value"]["value"]
            except Exception as e:  # noqa: BLE001
                log(f"Instagram: Metrik {metric} nicht verfügbar ({e})")
        out["instagram"] = ig
    else:
        log("Instagram: kein Konto mit der Facebook-Seite verknüpft")
    return out


def youtube():
    key, ch = os.environ.get("YOUTUBE_API_KEY"), CFG.get("youtube_channel_id")
    if not key or not ch:
        log("YouTube: YOUTUBE_API_KEY oder youtube_channel_id fehlt - übersprungen")
        return {}
    r = http("https://www.googleapis.com/youtube/v3/channels?" + urllib.parse.urlencode(
        {"part": "statistics", "id": ch, "key": key}))
    s = r["items"][0]["statistics"]
    return {"youtube": {"followers": int(s.get("subscriberCount", 0)),
                        "views_total": int(s.get("viewCount", 0)),
                        "videos": int(s.get("videoCount", 0))}}


def tiktok_api():
    ck, cs = os.environ.get("TIKTOK_CLIENT_KEY"), os.environ.get("TIKTOK_CLIENT_SECRET")
    rt = os.environ.get("TIKTOK_REFRESH_TOKEN")
    if not (ck and cs and rt):
        return None
    tok = http("https://open.tiktokapis.com/v2/oauth/token/", data={
        "client_key": ck, "client_secret": cs,
        "grant_type": "refresh_token", "refresh_token": rt},
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    if "access_token" not in tok:
        raise RuntimeError(f"Token-Erneuerung fehlgeschlagen: {tok}")
    if tok.get("refresh_token") and tok["refresh_token"] != rt:
        # Der Workflow speichert den neuen Refresh-Token zurück in die Secrets.
        with open(os.path.join(ROOT, ".new_tiktok_refresh_token"), "w") as f:
            f.write(tok["refresh_token"])
    auth = {"Authorization": f"Bearer {tok['access_token']}"}
    u = http("https://open.tiktokapis.com/v2/user/info/?fields=follower_count,likes_count,video_count",
             headers=auth)["data"]["user"]
    views, cursor = 0, None
    for _ in range(25):  # max. 500 Videos
        body = {"max_count": 20}
        if cursor:
            body["cursor"] = cursor
        r = http("https://open.tiktokapis.com/v2/video/list/?fields=id,view_count",
                 data=json.dumps(body).encode(),
                 headers={**auth, "Content-Type": "application/json"})["data"]
        views += sum(v.get("view_count", 0) for v in r.get("videos", []))
        if not r.get("has_more"):
            break
        cursor = r.get("cursor")
    return {"followers": u.get("follower_count"), "likes": u.get("likes_count"),
            "videos": u.get("video_count"), "views_total": views}


def tiktok():
    try:
        d = tiktok_api()
        if d:
            return {"tiktok": d}
    except Exception as e:  # noqa: BLE001
        log(f"TikTok-API: {e} - weiche auf öffentliche Profilzahlen aus")
    html = http(CFG["tiktok_url"], ua=BROWSER_UA, as_json=False)
    g = lambda k: (re.search(rf'"{k}":(\d+)', html) or [None, None])[1]  # noqa: E731
    if not g("followerCount"):
        raise RuntimeError("öffentliche Profilzahlen nicht gefunden")
    return {"tiktok": {"followers": int(g("followerCount")), "likes": int(g("heartCount") or 0),
                       "videos": int(g("videoCount") or 0)}}


def soundcloud():
    html = http(CFG["soundcloud_url"], ua=BROWSER_UA, as_json=False)
    f = re.search(r'"followers_count":(\d+)', html)
    t = re.search(r'"track_count":(\d+)', html)
    if not f:
        raise RuntimeError("Follower-Zahl nicht gefunden")
    return {"soundcloud": {"followers": int(f[1]), "tracks": int(t[1]) if t else None}}


def deezer():
    r = http(f"https://api.deezer.com/artist/{CFG['deezer_artist_id']}")
    return {"deezer": {"followers": r.get("nb_fan"), "albums": r.get("nb_album")}}


def whatsapp():
    html = http(CFG["whatsapp_url"], ua=BROWSER_UA, as_json=False)
    m = re.search(r"([\d.,]+\s*[KkMm]?)\s*(?:followers|Abonnenten|Follower)", html)
    if not m:
        raise RuntimeError("Follower-Zahl nicht gefunden")
    return {"whatsapp": {"followers": num(m[1])}}


# ---------------------------------------------------------- Spotify (CSV-Import)

def _pick(headers, *needles):
    for h in headers:
        hl = h.lower()
        if any(n in hl for n in needles):
            return h
    return None


def _parse_date(s):
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            pass
    return None


def spotify_imports(hist):
    """Liest alle CSV-Exporte aus Spotify for Artists in data/imports/spotify/."""
    daily = hist.setdefault("spotify_daily", {})
    for path in sorted(glob.glob(os.path.join(DATA, "imports", "spotify", "*.csv"))):
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            continue
        hdr = list(rows[0].keys())
        dcol = _pick(hdr, "date", "datum")
        cols = {"listeners": _pick(hdr, "listener", "hörer", "hoerer"),
                "streams": _pick(hdr, "stream"),
                "followers": _pick(hdr, "follower")}
        if not dcol or not any(cols.values()):
            continue
        n = 0
        for r in rows:
            d = _parse_date(r.get(dcol, ""))
            if not d:
                continue
            day = daily.setdefault(d, {})
            for k, c in cols.items():
                if c and str(r.get(c, "")).strip():
                    day[k] = num(r[c])
            n += 1
        log(f"Spotify-Import: {os.path.basename(path)} ({n} Tage)")


def playlists():
    """Wandelt data/imports/playlists*.csv in eine Tabelle für das Dashboard."""
    files = sorted(glob.glob(os.path.join(DATA, "imports", "playlists*.csv")) +
                   glob.glob(os.path.join(DATA, "imports", "spotify", "playlists*.csv")))
    if not files:
        return None
    path = files[-1]
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return None
    return {"source": os.path.basename(path),
            "updated": dt.datetime.fromtimestamp(os.path.getmtime(path)).date().isoformat(),
            "headers": rows[0], "rows": [r for r in rows[1:] if any(c.strip() for c in r)]}


# ------------------------------------------------------------------- Ablauf

def main():
    os.makedirs(DATA, exist_ok=True)
    hist = json.load(open(HISTORY, encoding="utf-8")) if os.path.exists(HISTORY) else {}
    hist.setdefault("snapshots", {})
    today = dt.date.today().isoformat()
    snap = {}
    for name, fn in [("Meta", meta), ("YouTube", youtube), ("TikTok", tiktok),
                     ("SoundCloud", soundcloud), ("Deezer", deezer), ("WhatsApp", whatsapp)]:
        try:
            snap.update(fn())
        except Exception as e:  # noqa: BLE001
            log(f"{name}: Fehler - {e}")
    # Werte, die nicht geholt werden konnten, nicht mit leeren überschreiben
    old = hist["snapshots"].get(today, {})
    old.update({k: {kk: vv for kk, vv in v.items() if vv is not None} for k, v in snap.items()})
    hist["snapshots"][today] = old

    spotify_imports(hist)
    hist["playlists"] = playlists()
    hist["updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    hist["log"] = LOG
    hist["config"] = {"band": CFG.get("band"), "links": CFG.get("links", {})}

    with open(HISTORY, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(json.dumps(old, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
