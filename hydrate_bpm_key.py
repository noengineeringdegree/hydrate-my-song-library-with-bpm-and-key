"""
hydrate_bpm_key.py - add BPM, key and Camelot to the catalog, politely.

Source: GetSongBPM API (free; requires a key and a backlink to getsongbpm.com,
see https://getsongbpm.com/api). Values are for PLANNING crates before you buy.
After download, rekordbox's own analysis is the source of truth.

Usage (run on your own computer, Python 3.9+, no extra packages):
    setx GETSONGBPM_API_KEY "your_key"     (Windows; then open a new terminal)
    python hydrate_bpm_key.py              (default: up to 300 lookups this run)
    python hydrate_bpm_key.py --limit 50   (small test first)

It is resumable: rerun any time and it skips rows already looked up.
Output: <project>/agents downloads/catalog_enriched.csv. The master catalog is never modified.
"""
import argparse, csv, difflib, json, os, random, re, sys, time
import urllib.error, urllib.parse, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
def find_root(start):
    """Walk up from the script until we find the folder holding Catalogue/."""
    env = os.environ.get("DJ_PROJECT_ROOT")
    if env:
        return Path(env)
    for p in [start, *start.parents]:
        if (p / "Catalogue").is_dir():
            return p
    sys.exit("Can't find the Catalogue folder above this script. Set DJ_PROJECT_ROOT.")
ROOT = find_root(HERE)
MASTER = ROOT / "Catalogue" / "catalog_master.csv"
OUT = ROOT / "agents downloads" / "catalog_enriched.csv"
BASE = "https://api.getsong.co/search/"
NEW_COLS = ["bpm", "key", "camelot", "match_artist", "match_title",
            "match_score", "lookup_status"]

# ---------------- pacing ----------------
class Pacer:
    """Min interval + jitter, backoff on 429/5xx, hard stop on auth errors."""
    def __init__(self, min_interval=1.0, jitter=0.5, max_retries=5):
        self.min_interval, self.jitter, self.max_retries = min_interval, jitter, max_retries
        self.last = 0.0

    def wait(self):
        gap = self.min_interval + random.uniform(0, self.jitter)
        sleep = self.last + gap - time.monotonic()
        if sleep > 0:
            time.sleep(sleep)
        self.last = time.monotonic()

    def get_json(self, url):
        delay = 5.0
        for attempt in range(self.max_retries):
            self.wait()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "dj-catalog-hydrator/0.1"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    sys.exit(f"Auth error {e.code}: check your API key. Stopping, no retries.")
                if e.code == 429 or e.code >= 500:
                    ra = e.headers.get("Retry-After")
                    wait = float(ra) if ra and ra.isdigit() else delay
                    print(f"  server said slow down ({e.code}); waiting {wait:.0f}s")
                    time.sleep(wait)
                    delay = min(delay * 2, 600)
                    continue
                return {"_error": f"http {e.code}"}
            except (urllib.error.URLError, TimeoutError) as e:
                print(f"  network issue ({e}); waiting {delay:.0f}s")
                time.sleep(delay)
                delay = min(delay * 2, 600)
        return {"_error": "gave up after retries"}

# ---------------- key handling ----------------
NOTE = {"C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5, "F#": 6,
        "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10, "BB": 10, "B": 11}
# Camelot numbers: major keys (B) and minor keys (A), indexed by pitch class
CAM_MAJ = {0: 8, 7: 9, 2: 10, 9: 11, 4: 12, 11: 1, 6: 2, 1: 3, 8: 4, 3: 5, 10: 6, 5: 7}
CAM_MIN = {9: 8, 4: 9, 11: 10, 6: 11, 1: 12, 8: 1, 3: 2, 10: 3, 5: 4, 0: 5, 7: 6, 2: 7}

def to_camelot(key_of, open_key):
    ok = re.fullmatch(r"\s*(\d{1,2})\s*([dm])\s*", str(open_key or ""), re.I)
    if ok:  # Open Key 1d = C major = 8B; shift by 7
        n = (int(ok.group(1)) - 1 + 7) % 12 + 1
        return f"{n}{'B' if ok.group(2).lower() == 'd' else 'A'}"
    k = str(key_of or "").replace("♯", "#").replace("♭", "b").strip()
    m = re.fullmatch(r"([A-Ga-g])([#b]?)\s*(m|min|minor|maj|major)?", k)
    if not m:
        return ""
    pc = NOTE.get((m.group(1) + m.group(2)).upper())
    if pc is None:
        return ""
    minor = (m.group(3) or "").lower() in ("m", "min", "minor")
    return f"{CAM_MIN[pc]}A" if minor else f"{CAM_MAJ[pc]}B"

# ---------------- matching ----------------
def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", str(s).lower())).strip()

def best_match(results, artist, title):
    best, best_score = None, 0.0
    for r in results if isinstance(results, list) else []:
        r_artist = (r.get("artist") or {}).get("name", "")
        a = difflib.SequenceMatcher(None, norm(artist), norm(r_artist)).ratio()
        t = difflib.SequenceMatcher(None, norm(title), norm(r.get("title", ""))).ratio()
        score = 0.4 * a + 0.6 * t
        if score > best_score:
            best, best_score = r, score
    return best, round(best_score, 2)

# ---------------- main ----------------
def load_rows():
    src = OUT if OUT.exists() else MASTER
    with open(src, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for c in NEW_COLS:
            r.setdefault(c, "")
    return rows

def save(rows):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    tmp.replace(OUT)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=300, help="max lookups this run (daily cap)")
    ap.add_argument("--min-score", type=float, default=0.75, help="below this = low_confidence")
    ap.add_argument("--retry-missing", action="store_true", help="retry not_found/error rows")
    args = ap.parse_args()

    key = os.environ.get("GETSONGBPM_API_KEY")
    if not key:
        sys.exit("Set GETSONGBPM_API_KEY first (see top of file).")

    rows = load_rows()
    redo = {"", "error"} | ({"not_found"} if args.retry_missing else set())
    todo = [r for r in rows if r["lookup_status"] in redo]
    print(f"{len(rows)} tracks, {len(todo)} to look up, doing up to {args.limit} this run.")

    pacer, done = Pacer(), 0
    for r in todo[: args.limit]:
        artist, title = r["primary_artist"], r["base_title"] or r["title"]
        q = urllib.parse.urlencode({"api_key": key, "type": "both",
                                    "lookup": f"song:{title} artist:{artist}"})
        data = pacer.get_json(f"{BASE}?{q}")
        if "_error" in data:
            r["lookup_status"] = "error"
        else:
            hit, score = best_match(data.get("search"), artist, title)
            if not hit:
                r["lookup_status"] = "not_found"
            else:
                r["bpm"] = hit.get("tempo", "")
                r["key"] = hit.get("key_of", "")
                r["camelot"] = to_camelot(hit.get("key_of"), hit.get("open_key"))
                r["match_artist"] = (hit.get("artist") or {}).get("name", "")
                r["match_title"] = hit.get("title", "")
                r["match_score"] = score
                r["lookup_status"] = "ok" if score >= args.min_score else "low_confidence"
        done += 1
        print(f"[{done}/{min(len(todo), args.limit)}] {artist} - {title}: "
              f"{r['lookup_status']} {r['bpm']} {r['camelot']}")
        if done % 10 == 0:
            save(rows)
    save(rows)
    counts = {}
    for r in rows:
        counts[r["lookup_status"] or "pending"] = counts.get(r["lookup_status"] or "pending", 0) + 1
    print("Saved", OUT.name, counts)

if __name__ == "__main__":
    main()
