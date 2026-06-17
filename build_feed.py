#!/usr/bin/env python3
"""
EBS rundown feed builder.
Modes:
  python build_feed.py local ebs.json ebsplus.json   -> build feed.json from saved files
  python build_feed.py                               -> fetch live from the API
"""
import json, re, sys, html, gzip, time, datetime
import urllib.request, urllib.error, logging

API_BASE = "https://8hwk2cyeyb.execute-api.eu-west-1.amazonaws.com/parrotfish-prod/grid"
CHANNELS = ["ebs", "ebsplus"]
DAYS_AHEAD = 6
OLD_TIME = "2026-01-01T00:00:00+00:00"
INST_MAP = {"2618": "EP", "2620": "EC", "2619": "Council", "58817": "Host"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip",
    "Accept-Language": "en",
    "Origin": "https://audiovisual.ec.europa.eu",
    "Referer": "https://audiovisual.ec.europa.eu/",
}

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

def clean(s):
    if not s: return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()

def pick_en(items, key="content"):
    if not items: return ""
    for it in items:
        if it.get("language") == "EN": return clean(it.get(key, ""))
    return clean(items[0].get(key, ""))

def get_inst_code(media):
    for inst in media.get("institutions", []):
        iid = str(inst.get("id"))
        if iid in INST_MAP: return INST_MAP[iid]
        title = next((x.get("content", "") for x in inst.get("titles", []) if x.get("language") == "EN"), "")
        if title: return clean(title)[:18]
    return ""

def get_mmc_link(media):
    for link in media.get("links", []):
        href = link.get("href", "")
        if "multimedia.europarl.europa.eu" in href: return href
    return ""

def parse_grid(data):
    out = []
    for day in data:
        ch_name = day.get("channel", {}).get("name", "?")
        for program in day.get("programs", []):
            start = program.get("startDatetime")
            if not start: continue
            duration = int(round(program.get("duration", 0)))
            subs, seen_refs = [], set()
            for transmission in program.get("transmissions", []):
                media = transmission.get("media", {})
                ref = media.get("reference")
                if not ref or ref in seen_refs: continue
                seen_refs.add(ref)
                item = {"ref": ref, "title": pick_en(media.get("titles", [])), "summary": pick_en(media.get("summaries", []))}
                inst_code = get_inst_code(media)
                if inst_code: item["inst"] = inst_code
                mmc_link = get_mmc_link(media)
                if mmc_link: item["mmc"] = mmc_link
                subs.append(item)
            is_ep = any(s.get("inst") == "EP" or s.get("mmc") for s in subs)
            out.append({
                "ep": is_ep, "start": start, "durationSec": duration,
                "channel": ch_name, "status": program.get("broadcastStatus", ""),
                "title": pick_en(program.get("titles", [])), "languages": program.get("languages", []),
                "items": subs[:15],
            })
    return out

def fetch_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()
        cenc = response.headers.get("Content-Encoding", "")
        status = getattr(response, "status", "?")
        ctype = response.headers.get("Content-Type", "?")
    if cenc == "gzip" or raw.startswith(b"\x1f\x8b"): raw = gzip.decompress(raw)
    text = raw.decode("utf-8-sig", errors="replace")
    try: return json.loads(text)
    except json.JSONDecodeError: raise RuntimeError(f"Not JSON (status {status}, type {ctype}).")

def main():
    events = []
    if len(sys.argv) >= 2 and sys.argv[1] == "local":
        logging.info("Running in LOCAL mode")
        for path in sys.argv[2:]:
            with open(path, encoding="utf-8") as f: events.extend(parse_grid(json.load(f)))
    else:
        logging.info("Running in API mode")
        today = datetime.datetime.now(datetime.timezone.utc).date()
        failures = 0
        for ch in CHANNELS:
            for offset in range(DAYS_AHEAD + 1):
                target_date = (today + datetime.timedelta(days=offset)).strftime("%Y%m%d")
                url = f"{API_BASE}?channelName={ch}&dateFrom={target_date}&dateTo={target_date}&thesaurusAsObject=true"
                for attempt in (1, 2):
                    try:
                        events.extend(parse_grid(fetch_json(url)))
                        break
                    except Exception as e:
                        if attempt == 2:
                            failures += 1
                            logging.warning(f"Failed {ch} for {target_date}: {e}")
                        else: time.sleep(3)
                time.sleep(0.5)
        if not events: sys.exit(f"Critical Failure: All requests failed ({failures} errors).")
        if failures: logging.info(f"Completed with {failures} failed day(s); feed written from successful requests.")

    events.sort(key=lambda e: e["start"])
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    prev_stamps = {}
    
    try:
        with open("feed.json", encoding="utf-8") as f0:
            for old_event in json.load(f0).get("events", []):
                key = f"{old_event['start']}|{old_event['channel']}|{old_event['title']}"
                prev_stamps[key] = old_event.get("firstSeen", OLD_TIME)
    except Exception: pass
        
    for event in events:
        key = f"{event['start']}|{event['channel']}|{event['title']}"
        event["firstSeen"] = prev_stamps.get(key, now_iso if prev_stamps else OLD_TIME)

    feed = {"generatedAt": now_iso, "events": events}
    with open("feed.json", "w", encoding="utf-8") as f:
        json.dump(feed, f, ensure_ascii=False, indent=1)
    logging.info(f"Success: feed.json written with {len(events)} events.")

if __name__ == "__main__":
    main()