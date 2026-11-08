# WUHA Statistik-Dashboard – Einrichtung

Das Dashboard läuft kostenlos auf **GitHub**:

- **GitHub Pages** zeigt die Seite unter einem Link wie `https://<euer-name>.github.io/wuha-dashboard/`.
- **GitHub Actions** ruft jeden Morgen die Zahlen ab und speichert sie in `data/history.json`.
  Daraus berechnet das Dashboard Wochen- und Monatswerte.

> Hinweis: Bei kostenlosem GitHub Pages ist das Repository **öffentlich**. Follower-Zahlen sind ohnehin
> öffentlich; Reichweite/Aufrufe von Instagram & Facebook wären es dann auch. Zugangsdaten (Tokens)
> liegen **nicht** im Code, sondern geschützt in den GitHub-Secrets.

## Was schon ohne Zugangsdaten funktioniert

| Kanal | Woher |
|---|---|
| TikTok (Follower, Likes, Videos) | öffentliches Profil |
| SoundCloud (Follower, Tracks) | öffentliches Profil |
| Deezer (Fans, Releases) | offene Deezer-Schnittstelle |
| WhatsApp-Kanal (Follower) | öffentliche Kanalseite |

Apple Music, Amazon Music und TIDAL bieten keine Statistik-Schnittstelle für Bands; sie stehen als Links im Dashboard.

---

## Schritt 1 – GitHub (ca. 10 Min.)

1. Konto anlegen auf https://github.com (falls noch keins).
2. Rechts oben **+ → New repository** → Name `wuha-dashboard` → **Public** → *Create repository*.
3. Auf der leeren Repo-Seite **„uploading an existing file“** klicken und den **gesamten Inhalt** des Ordners
   `wuha-dashboard` hineinziehen (inkl. des versteckten Ordners `.github` – am Mac im Finder mit
   `Cmd + Shift + .` sichtbar machen). → *Commit changes*.
4. **Settings → Pages** → *Source: Deploy from a branch* → Branch `main`, Ordner `/ (root)` → *Save*.
   Nach 1–2 Minuten steht oben der Link zu eurem Dashboard.
5. **Settings → Actions → General** → ganz unten *Workflow permissions* → **Read and write permissions** → *Save*.
6. **Actions → „Zahlen sammeln“ → Run workflow** – damit wird sofort der erste Datenstand geholt.

Ab jetzt läuft das jeden Morgen von allein.

## Schritt 2 – Instagram + Facebook (ca. 20 Min.)

Voraussetzung: Das Instagram-Creator-Konto ist mit der Facebook-Seite *wuhamusic* verknüpft
(Instagram → Einstellungen → Kontenübersicht bzw. in der Meta Business Suite).

1. https://developers.facebook.com → **Meine Apps → App erstellen** → Anwendungsfall
   *„Andere“* → Typ **Business** → Name z. B. `WUHA Dashboard`.
2. Im **Graph API Explorer** (Tools → Graph API Explorer):
   - rechts eure App auswählen,
   - *User or Page* → **Get Page Access Token**,
   - Berechtigungen: `pages_show_list`, `pages_read_engagement`, `read_insights`,
     `instagram_basic`, `instagram_manage_insights`, `business_management`,
   - *Generate Access Token* → mit dem Facebook-Konto bestätigen, das die Seite verwaltet.
3. Den Token **dauerhaft** machen: Im **Access Token Debugger** (Tools → Access Token Debugger)
   den Token prüfen → *Extend Access Token*. Danach im Graph API Explorer `me/accounts` abfragen –
   der dort angezeigte `access_token` der Seite *WUHA* läuft **nicht ab**. Die `id` der Seite notieren.
4. In GitHub: **Settings → Secrets and variables → Actions → New repository secret**
   - Name `META_PAGE_TOKEN`, Wert = der Seiten-Token.
5. In `config.json` bei `"facebook_page_id"` die Seiten-ID eintragen (direkt auf GitHub mit dem Stift-Symbol bearbeiten).

## Schritt 3 – YouTube (ca. 5 Min.)

1. https://console.cloud.google.com → neues Projekt `wuha-dashboard`.
2. **APIs & Dienste → Bibliothek → „YouTube Data API v3“ → Aktivieren**.
3. **Anmeldedaten → Anmeldedaten erstellen → API-Schlüssel**. Den Schlüssel einschränken auf
   *YouTube Data API v3*.
4. GitHub-Secret `YOUTUBE_API_KEY` mit dem Schlüssel anlegen.
5. In `config.json` bei `"youtube_channel_id"` die Kanal-ID eintragen (beginnt mit `UC…`,
   zu finden in YouTube Studio → Einstellungen → Kanal → Erweiterte Einstellungen).

## Schritt 4 – TikTok-Aufrufe (optional, ca. 30 Min. + Wartezeit)

Follower/Likes kommen schon vom öffentlichen Profil. Für die **Aufrufe** braucht es die offizielle Schnittstelle:

1. https://developers.tiktok.com → App anlegen → Produkt **Login Kit** hinzufügen,
   Scopes `user.info.stats` und `video.list`.
2. Im **Sandbox-Modus** euer TikTok-Konto als Test-Nutzer eintragen (reicht für eigene Daten).
3. Einmalig anmelden, um einen Refresh-Token zu bekommen (dabei helfe ich gern Schritt für Schritt).
4. GitHub-Secrets: `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REFRESH_TOKEN`.
5. TikTok erneuert den Refresh-Token regelmäßig. Damit das Dashboard ihn selbst aktualisieren kann:
   GitHub → *Settings (eures Profils) → Developer settings → Fine-grained tokens* → Token nur für
   dieses Repository mit Recht **Secrets: Read and write** → als Secret `GH_PAT` speichern.

## Schritt 5 – Spotify (jede Woche oder jeden Monat, 2 Min.)

Spotify gibt Streams, Hörer und Playlisten **nicht** über eine Schnittstelle heraus.

**Streams / Hörer / Follower:**
1. https://artists.spotify.com → **Publikum** → Zeitraum (z. B. *Letzte 28 Tage* oder *Seit 2025*) →
   Download-Symbol → CSV.
2. Auf GitHub in den Ordner `data/imports/spotify/` hochladen (*Add file → Upload files*).
   Das Dashboard liest die Datei sofort ein; ältere Dateien können liegen bleiben.

**Playlisten:**
1. Spotify for Artists → **Musik → Playlisten** zeigt alle Playlisten, in denen ihr seid.
2. Die Datei `data/imports/playlists.csv` auf GitHub bearbeiten und pro Playlist eine Zeile eintragen,
   z. B. `Vienna Indie Picks,Kuratorname,Rainbow Eyes,12400,2026-10-02,https://open.spotify.com/playlist/…`
   (oder eine eigene CSV mit Namen `playlists….csv` hochladen – die Spalten werden automatisch übernommen).

---

## Gut zu wissen

- **Verläufe** beginnen am Tag der Einrichtung. Wochen-/Monatsvergleiche sind nach 7 bzw. 30 Tagen vollständig.
- Unten im Dashboard zeigt **„Protokoll der letzten Datenabfrage“**, ob ein Kanal Probleme hatte.
- Öffentliche Profilseiten (TikTok, SoundCloud, WhatsApp) können ihr Format ändern – dann fehlt der Wert
  einen Tag lang im Protokoll, bis das Skript angepasst ist.
