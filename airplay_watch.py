#!/usr/bin/env python3
"""Radio-Airplays für WUHA – liest Airplay-Mails aus Gmail und schreibt data/airplays.json.

Erkennt (auch weitergeleitet):
  * Amazing Radio / Amazing Radio USA – "Your last week on Amazing Radio"
Gmail-Suche: Betreff/Text mit "airplays in the last week" ODER Label "Airplay".
Doppelte Einträge (gleicher Sender, Tag, Uhrzeit, Song) werden nur einmal gezählt.

Von Hand ergänzen: data/imports/airplays_manuell.csv
  Spalten: Datum (JJJJ-MM-TT), Uhrzeit, Song, Sender, Sendung/Stream, Land
Secrets: GMAIL_USER, GMAIL_APP_PASSWORD (wie beim Playlist-Check)
"""
import csv
import datetime as dt
import email
import email.header
import imaplib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "data", "airplays.json")
MANUAL = os.path.join(ROOT, "data", "imports", "airplays_manuell.csv")
QUERY = 'label:airplay OR "airplays in the last week"'
LOG = []
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                         "september", "october", "november", "december"], 1)}


def log(msg):
    LOG.append(str(msg)[:300])
    print(msg, file=sys.stderr)


def _text(msg):
    plain, htm = "", ""
    for p in msg.walk():
        if p.get_content_maintype() != "text" or p.get_filename():
            continue
        s = (p.get_payload(decode=True) or b"").decode(p.get_content_charset() or "utf-8", "replace")
        if p.get_content_subtype() == "html":
            htm += s
        else:
            plain += s
    if plain.strip():
        return plain
    htm = re.sub(r"(?is)<(script|style).*?</\1>", " ", htm)
    htm = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|h\d)>", "\n", htm)
    htm = re.sub(r"(?i)</td>", "\t", htm)
    return re.sub(r"<[^>]+>", "", htm)


def parse_amazing(text):
    """Wochen-Mail von Amazing Radio (UK/USA). Mehrere weitergeleitete Mails in einer Mail sind ok."""
    out = []
    # Text in Blöcke je Mail teilen (jede beginnt mit "Dear ..." / "YOUR WEEK ON RADIO")
    blocks = re.split(r"(?i)YOUR WEEK ON RADIO", text)
    for b in blocks[1:]:
        station = "Amazing Radio USA" if re.search(r"Amazing Radio USA|amazingradio\.us", b) else "Amazing Radio"
        country = "USA" if station.endswith("USA") else "UK"
        day = None
        for raw in b.splitlines():
            line = re.sub(r"^[>\s]+", "", raw).strip()
            line = re.sub(r"\s*<https?://[^>]+>", "", line)       # Links entfernen
            m = re.match(r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$", line)
            if m and m.group(2).lower() in MONTHS:
                day = dt.date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1))).isoformat()
                continue
            m = re.match(r"^(\d{1,2}:\d{2})\s*\t\s*(.+?)\s*\t\s*([^\t]+?)\s*(?:\t\s*(.*))?$", line)
            if m and day:
                out.append({"date": day, "time": m.group(1), "song": m.group(2).strip(), "station": station,
                            "show": (m.group(4) or "").strip() or f"{m.group(3).strip()} Stream", "country": country,
                            "tz": "GMT", "source": "Amazing Radio Mail"})
    return out


def gmail():
    user, pw = os.environ.get("GMAIL_USER", "").strip(), os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not (user and pw):
        log("Gmail: Zugangsdaten fehlen – Airplay-Mails nicht gelesen.")
        return [], []
    im = imaplib.IMAP4_SSL("imap.gmail.com")
    im.login(user, pw)
    allmail = None
    for line in im.list()[1]:
        s = line.decode("utf-8", "replace")
        if "\\All" in s:
            allmail = s.split(' "/" ')[-1].strip()
    im.select(allmail or "INBOX", readonly=True)
    typ, data = im.search(None, "X-GM-RAW", '"' + QUERY.replace('"', '\\"') + '"')
    ids = data[0].split() if typ == "OK" and data and data[0] else []
    plays, mids = [], []
    for num in ids:
        typ, md = im.fetch(num, "(X-GM-MSGID RFC822)")
        if typ != "OK":
            continue
        mid = re.search(rb"X-GM-MSGID (\d+)", md[0][0])
        mids.append(mid.group(1).decode() if mid else num.decode())
        msg = email.message_from_bytes(md[0][1])
        plays += parse_amazing(_text(msg))
    im.logout()
    log(f"Gmail: {len(ids)} Airplay-Mails gelesen, {len(plays)} Einträge gefunden.")
    return plays, mids


def manual():
    rows = []
    if os.path.exists(MANUAL):
        with open(MANUAL, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
                if r.get("datum") and r.get("song"):
                    rows.append({"date": r["datum"], "time": r.get("uhrzeit", ""), "song": r["song"],
                                 "station": r.get("sender", "") or "?", "show": r.get("sendung/stream", r.get("sendung", "")),
                                 "country": r.get("land", ""), "tz": "", "source": "manuell"})
    return rows


def main():
    state = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    plays = state.get("plays", [])
    new, _ = [], []
    try:
        new, _ = gmail()
    except Exception as e:  # noqa: BLE001
        log(f"Gmail: Fehler – {e}")
    seen = {(p["station"], p["date"], p["time"], p["song"].lower()) for p in plays}
    added = 0
    for p in new + manual():
        k = (p["station"], p["date"], p["time"], p["song"].lower())
        if k not in seen:
            seen.add(k)
            plays.append(p)
            added += 1
    plays.sort(key=lambda p: (p["date"], p["time"]), reverse=True)
    by_station, by_song = {}, {}
    for p in plays:
        by_station[p["station"]] = by_station.get(p["station"], 0) + 1
        by_song[p["song"]] = by_song.get(p["song"], 0) + 1
    today = dt.date.today()
    last = lambda d: sum(1 for p in plays if p["date"] >= (today - dt.timedelta(days=d)).isoformat())
    state.update({"plays": plays, "summary": {"total": len(plays), "last7": last(7), "last30": last(30),
                                              "stations": by_station, "songs": by_song},
                  "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"), "log": LOG})
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    print(json.dumps({"neu": added, "gesamt": len(plays)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
