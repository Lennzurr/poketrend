#!/usr/bin/env python3
"""Relevé horaire des prix de toutes les cartes Pokémon (pour PokéTrend).

Lancé par GitHub Actions toutes les heures. Lit l'état précédent dans le dossier
de sortie (branche « data »), récupère les prix sur l'API Pokémon TCG, puis écrit :
  latest.json        cartes + prix actuels + prix de référence (il y a 1 h, 24 h, 7 j, 30 j)
  hist/<set>.json    historique des prix par extension (pour les courbes)
  history.json.gz    état interne (tous les relevés conservés)
Uniquement la bibliothèque standard Python.
"""
import gzip, json, os, random, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

API = os.environ.get("PT_API", "https://api.pokemontcg.io/v2")  # PT_API : tests uniquement
SELECT = "id,name,number,rarity,set,images,tcgplayer,cardmarket"
PAGE_SIZE = 250
KEY = os.environ.get("POKEMONTCG_API_KEY", "").strip()
HOUR, DAY = 3600, 86400
TCG_ORDER = ["holofoil", "normal", "reverseHolofoil", "1stEditionHolofoil",
             "1stEditionNormal", "unlimitedHolofoil", "1stEdition", "unlimited"]
IMG_PREFIX = "https://images.pokemontcg.io/"

out = sys.argv[1] if len(sys.argv) > 1 else "out"
os.makedirs(os.path.join(out, "hist"), exist_ok=True)
now = int(os.environ.get("PT_NOW") or time.time())  # PT_NOW : tests uniquement
START = time.time()
BUDGET = float(os.environ.get("PT_BUDGET", 38 * 60))  # on arrête de charger après ~38 min pour toujours publier


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def get(path, tries=12):
    """GET avec réessais : l'API renvoie souvent des erreurs passagères."""
    headers = {"User-Agent": "PokeTrend-collector/1.0"}
    if KEY:
        headers["X-Api-Key"] = KEY
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(API + path, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (401, 403):
                raise SystemExit(f"Clé API refusée ({e.code}). Vérifie le secret POKEMONTCG_API_KEY.")
            if e.code == 429:
                time.sleep(20)
        except Exception as e:  # réseau, délai dépassé, JSON tronqué…
            last = e
        time.sleep(float(os.environ.get("PT_SLEEP_SCALE", "1")) * (1.5 + random.random() * 2.5 + attempt * 0.5))
    raise RuntimeError(f"échec après {tries} essais : {path} ({last})")


def num(v):
    return round(float(v), 2) if isinstance(v, (int, float)) and v > 0 else None


def tcg_main(t):
    p = (t or {}).get("prices") or {}
    for k in TCG_ORDER:
        if num((p.get(k) or {}).get("market")):
            return num(p[k]["market"])
    for v in p.values():
        if num((v or {}).get("market")):
            return num(v["market"])
    for v in p.values():
        if num((v or {}).get("mid")):
            return num(v["mid"])
    return None


def row(c):
    s = c.get("set") or {}
    cm = (c.get("cardmarket") or {}).get("prices") or {}
    img = ((c.get("images") or {}).get("small") or "")
    default = f"{IMG_PREFIX}{s.get('id')}/{c.get('number')}.png"
    img = "" if img == default else img.replace(IMG_PREFIX, "")
    return [c["id"], c.get("name"), c.get("number"), c.get("rarity") or "—", s.get("id"), img,
            num(cm.get("trendPrice")) or num(cm.get("averageSellPrice")),
            num(cm.get("avg1")), num(cm.get("avg7")), num(cm.get("avg30")), num(cm.get("lowPrice")),
            tcg_main(c.get("tcgplayer"))]


# ---------------------------------------------------------------- état précédent
state_path = os.path.join(out, "history.json.gz")
state = {"ids": [], "snaps": [], "lastFull": 0, "rows": {}}
if os.path.exists(state_path):
    try:
        with gzip.open(state_path, "rt", encoding="utf-8") as f:
            state.update(json.load(f))
        log(f"état précédent : {len(state['ids'])} cartes, {len(state['snaps'])} relevés")
    except Exception as e:
        log("état précédent illisible, on repart de zéro :", e)
rows = state["rows"]  # id -> ligne (dernière valeur connue)

# ---------------------------------------------------------------- extensions
sets, page = [], 1
while True:
    r = get(f"/sets?page={page}&pageSize=250&orderBy=-releaseDate")
    sets += r["data"]
    if len(sets) >= r["totalCount"] or not r["data"]:
        break
    page += 1
sets_out = [{"id": s["id"], "name": s["name"], "series": s.get("series"),
             "date": (s.get("releaseDate") or "").replace("/", "-"), "total": s.get("total")} for s in sets]
log(f"{len(sets_out)} extensions")

# ---------------------------------------------------------------- cartes
# Avec clé : toutes les cartes chaque heure.
# Sans clé (~1 000 requêtes/jour) : chaque heure, les 3 dernières extensions + 1/6 des autres pages,
# si bien que toutes les cartes sont rafraîchies en 6 heures. Premier relevé : tout, dans la limite de temps.
base = f"/cards?pageSize={PAGE_SIZE}&select={SELECT}&orderBy=set.releaseDate,number"


def fetch(path):
    r = get(path)
    for c in r["data"]:
        rows[c["id"]] = row(c)
    return r


def fetch_pages(b, pages, label):
    failed, done, skipped = [], 0, 0
    with ThreadPoolExecutor(max_workers=4 if KEY else 1) as ex:
        futs = {}
        for p in pages:
            futs[ex.submit(lambda p=p: None if time.time() - START > BUDGET else fetch(b + f"&page={p}"))] = p
        for fut in as_completed(futs):
            try:
                if fut.result() is None:
                    skipped += 1
                else:
                    done += 1
                    if done % 10 == 0:
                        log(f"{label} : {done}/{len(pages)} pages")
            except Exception as e:
                failed.append(str(e))
    log(f"{label} : {done} pages chargées, {len(failed)} en échec, {skipped} reportées (limite de temps)")
    return done, failed, skipped


first_run = not rows
first = fetch(base + "&page=1")
pages = -(-first["totalCount"] // PAGE_SIZE)
slot = (now // HOUR) % 6
if KEY or first_run:
    todo, mode = list(range(2, pages + 1)), "complet"
else:
    todo, mode = [p for p in range(2, pages + 1) if (p - 1) % 6 == slot], f"partiel {slot + 1}/6"
log(f"{mode} : {first['totalCount']} cartes, {len(todo) + 1} pages sur {pages}")
done, failed, skipped = fetch_pages(base, todo, "cartes")
full = mode == "complet" and not failed and not skipped
if full:
    state["lastFull"] = now

if not KEY and not first_run:
    q = " OR ".join(f"set.id:{s['id']}" for s in sets_out[:3])
    rb = base + "&q=" + urllib.parse.quote(q)
    try:
        r = fetch(rb + "&page=1")
        n = -(-r["totalCount"] // PAGE_SIZE)
        fetch_pages(rb, list(range(2, n + 1)), "dernières extensions")
    except Exception as e:
        log("dernières extensions en échec :", e)

# ---------------------------------------------------------------- relevé + historique
ids = state["ids"]
index = {cid: i for i, cid in enumerate(ids)}
for cid in rows:
    if cid not in index:
        index[cid] = len(ids)
        ids.append(cid)
snap = {"ts": now, "cm": [None] * len(ids), "tcg": [None] * len(ids)}
for cid, r in rows.items():
    snap["cm"][index[cid]] = r[6]
    snap["tcg"][index[cid]] = r[11]
snaps = [s for s in state["snaps"] if now - s["ts"] < 36 * DAY] + [snap]
snaps.sort(key=lambda s: s["ts"])
kept, days = [], set()
for s in snaps:  # tout sur 26 h, puis 1 relevé par jour sur 35 jours
    if now - s["ts"] <= 26 * HOUR:
        kept.append(s)
        continue
    d = time.strftime("%Y-%m-%d", time.gmtime(s["ts"]))
    if d not in days:
        days.add(d)
        kept.append(s)
state["snaps"] = kept


def find(target, tol):
    best, bd = None, None
    for s in kept:
        age = now - s["ts"]
        d = abs(age - target)
        if age > 20 * 60 and d <= tol and (bd is None or d < bd):
            best, bd = s, d
    return best


def val(s, src, i):
    if not s:
        return None
    arr = s[src]
    return arr[i] if i < len(arr) else None


refs_def = {"cm1h": (HOUR, 50 * 60, "cm"), "cm24h": (DAY, 6 * HOUR, "cm"),
            "tcg1h": (HOUR, 50 * 60, "tcg"), "tcg24h": (DAY, 6 * HOUR, "tcg"),
            "tcg7d": (7 * DAY, 1.5 * DAY, "tcg"), "tcg30d": (30 * DAY, 5 * DAY, "tcg")}
card_rows = sorted(rows.values(), key=lambda r: r[0])
refs = {}
for name, (target, tol, src) in refs_def.items():
    s = find(target, tol)
    refs[name] = [val(s, src, index[r[0]]) for r in card_rows] if s else None

latest = {"version": 1, "updated": now, "mode": mode,
          "sets": sets_out, "cards": card_rows, "refs": refs}
with open(os.path.join(out, "latest.json"), "w", encoding="utf-8") as f:
    json.dump(latest, f, ensure_ascii=False, separators=(",", ":"))

# Historique par extension (pour les courbes de PokéTrend)
by_set = {}
for r in card_rows:
    by_set.setdefault(r[4], []).append(r[0])
ts = [s["ts"] for s in kept]
for set_id, cids in by_set.items():
    c = {}
    for cid in cids:
        i = index[cid]
        c[cid] = [[val(s, "cm", i) for s in kept], [val(s, "tcg", i) for s in kept]]
    with open(os.path.join(out, "hist", f"{set_id}.json"), "w", encoding="utf-8") as f:
        json.dump({"ts": ts, "c": c}, f, separators=(",", ":"))

with gzip.open(state_path, "wt", encoding="utf-8") as f:
    json.dump(state, f, separators=(",", ":"))
log(f"terminé : {len(card_rows)} cartes, {len(kept)} relevés en historique")
