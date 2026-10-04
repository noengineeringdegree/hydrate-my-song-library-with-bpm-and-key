"""
build_crate.py - build a practice crate of tracks that actually mix together.

Reads <project>/agents downloads/catalog_enriched.csv (made by hydrate_bpm_key.py)
and writes an ordered crate to <project>/agents downloads/crates/<name>.csv.

Compatible = BPM within --tol percent (half/double time counts) AND a
harmonic key move: same Camelot key, +/-1 number, or the A/B swap
(+/-2 allowed as an "energy boost" at a higher cost).

Examples:
    python build_crate.py --seed "Modjo - Lady"                 # grow from a track
    python build_crate.py --bpm 120-128 --camelot 8A           # start from a zone
    python build_crate.py --seed "Daft Punk - One More Time" --genre house --size 40
    python build_crate.py --bpm 138-145 --genre bass --name peak_bass

Order in the output is a suggested mix path: each track mixes into the next.
"""
import argparse, csv, os, re, sys
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
DATA = ROOT / "agents downloads"
SRC = DATA / "catalog_enriched.csv"

def parse_cam(c):
    m = re.fullmatch(r"(\d{1,2})([AB])", str(c or "").strip().upper())
    return (int(m.group(1)), m.group(2)) if m else None

def key_cost(a, b):
    """0 = same key, 1 = adjacent/relative, 2 = energy boost, None = clash."""
    if not a or not b:
        return None
    (na, la), (nb, lb) = a, b
    step = min((na - nb) % 12, (nb - na) % 12)
    if la == lb:
        return {0: 0, 1: 1, 2: 2}.get(step)
    return 1 if step == 0 else None

def bpm_gap(a, b):
    """Smallest percent difference, allowing half/double time."""
    return min(abs(a * f - b) / b * 100 for f in (0.5, 1, 2))

def load(args):
    if not SRC.exists():
        sys.exit(f"Missing {SRC}. Run hydrate_bpm_key.py first.")
    rows = []
    with open(SRC, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            ok = {"ok"} | ({"low_confidence"} if args.include_low else set())
            if r.get("lookup_status") not in ok:
                continue
            try:
                r["_bpm"] = float(r["bpm"])
            except (TypeError, ValueError):
                continue
            r["_cam"] = parse_cam(r.get("camelot"))
            if not r["_cam"] or r["_bpm"] <= 0:
                continue
            if args.genre and args.genre.lower() not in (r.get("genre") or "").lower():
                continue
            if args.exclude_versions and r.get("version") in args.exclude_versions:
                continue
            rows.append(r)
    return rows

def find_seed(rows, text):
    t = text.lower()
    for r in rows:
        if r["track_id"] == text:
            return r
    hits = [r for r in rows if all(w in f"{r['primary_artist']} {r['title']}".lower()
                                   for w in re.findall(r"\w+", t))]
    if not hits:
        sys.exit(f"Seed '{text}' not found (or it has no BPM/key yet).")
    return max(hits, key=lambda r: float(r.get("popularity") or 0))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", help='"Artist - Title" words or a track_id')
    ap.add_argument("--bpm", help="range like 120-128 (used when no seed)")
    ap.add_argument("--camelot", help="starting key like 8A (used when no seed)")
    ap.add_argument("--genre", help="substring of genre bucket, e.g. house, garage, bass")
    ap.add_argument("--size", type=int, default=30)
    ap.add_argument("--tol", type=float, default=6.0, help="BPM tolerance in percent")
    ap.add_argument("--include-low", action="store_true", help="use low_confidence matches")
    ap.add_argument("--exclude-versions", nargs="*", default=["sped_slowed"])
    ap.add_argument("--name", help="output file name (no extension)")
    args = ap.parse_args()

    rows = load(args)
    if not rows:
        sys.exit("No tracks with BPM/key match those filters yet.")

    if args.seed:
        seed = find_seed(rows, args.seed)
    else:
        lo, hi = (float(x) for x in (args.bpm or "0-999").split("-"))
        cam = parse_cam(args.camelot)
        pool = [r for r in rows if lo <= r["_bpm"] <= hi
                and (not cam or key_cost(cam, r["_cam"]) == 0)]
        if not pool:
            sys.exit("Nothing in that BPM/key zone yet. Widen --bpm or drop --camelot.")
        seed = max(pool, key=lambda r: float(r.get("popularity") or 0))

    # Greedy mix path: always step to the cheapest compatible next track,
    # nudging toward tracks that stay close to the seed's tempo.
    crate, used, cur = [seed], {seed["track_id"]}, seed
    while len(crate) < args.size:
        best, best_cost = None, None
        for r in rows:
            if r["track_id"] in used:
                continue
            k = key_cost(cur["_cam"], r["_cam"])
            g = bpm_gap(cur["_bpm"], r["_bpm"])
            if k is None or g > args.tol:
                continue
            drift = bpm_gap(seed["_bpm"], r["_bpm"])
            cost = k * 2 + g * 0.5 + drift * 0.2 - float(r.get("popularity") or 0)
            if best_cost is None or cost < best_cost:
                best, best_cost = r, cost
        if not best:
            break
        crate.append(best)
        used.add(best["track_id"])
        cur = best

    name = args.name or re.sub(r"\W+", "_", f"{seed['primary_artist']}_{seed['base_title']}").strip("_")[:50]
    out = DATA / "crates" / f"{name}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["order", "primary_artist", "title", "bpm", "camelot", "genre", "version",
            "source_guess", "search_query", "track_id", "got_it"]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for i, r in enumerate(crate, 1):
            w.writerow({**r, "order": i, "got_it": ""})

    print(f"{len(crate)} tracks -> {out.relative_to(ROOT)}")
    for i, r in enumerate(crate, 1):
        print(f"{i:>3}. {r['bpm']:>4} {r['camelot']:>3}  {r['primary_artist']} - {r['title']}")
    if len(crate) < args.size:
        print(f"Stopped at {len(crate)}: no more compatible tracks. Try --tol 8 or --include-low.")

if __name__ == "__main__":
    main()
