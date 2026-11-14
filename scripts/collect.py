#!/usr/bin/env python3
"""Relevé des prix de toutes les cartes Pokémon pour PokéTrend (source : TCGdex, gratuit).

Lancé par GitHub Actions toutes les heures. Lit l'état précédent dans le dossier de
sortie (branche « data »), récupère les prix sur api.tcgdex.net, puis écrit :
  latest.json        cartes + prix actuels + prix de référence (il y a 1 h, 24 h, 7 j, 30 j)
  hist/<set>.json    historique des prix par extension (pour les courbes)
  history.json.gz    état interne (tous les relevés conservés)

TCGdex met à jour ses prix une fois par jour (vers 22 h 30 UTC). D'où :
  - relevé complet (toutes les cartes) une fois par jour, après cette mise à jour ;
  - chaque heure : détection des nouvelles extensions + prix des 3 dernières sorties ;
  - un relevé complet interrompu par la limite de temps reprend à l'heure suivante.
Uniquement la bibliothèque standard Python.
"""
import gzip, json, os, random, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

API = os.environ.get("PT_API", "https://api.tcgdex.net/v2/en")  # PT_API : tests uniquement
IMG_BASE = "https://assets.tcgdex.net/"
HOUR, DAY = 3600, 86400
FULL_HOUR_UTC = 23          # relevé complet du jour (la source se met à jour vers 22 h 30 UTC)
WORKERS = 6                 # requêtes en parallèle (rester raisonnable avec un service gratuit)
TCG_ORDER = ["holofoil", "normal", "reverse-holofoil", "1st-edition-holofoil", "1st-edition",
             "unlimited-holofoil", "unlimited", "1st-edition-normal"]

out = sys.argv[1] if len(sys.argv) > 1 else "out"
os.makedirs(os.path.join(out, "hist"), exist_ok=True)
now = int(os.environ.get("PT_NOW") or time.time())  # PT_NOW : tests uniquement
START = time.time()
BUDGET = float(os.environ.get("PT_BUDGET", 38 * 60))  # on arrête de charger après ~38 min pour toujours publier
SLEEP = float(os.environ.get("PT_SLEEP_SCALE", "1"))


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def get(path, tries=8):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(API + path, headers={"User-Agent": "PokeTrend-collector/2.0"})
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:
                return None
            if e.code == 429:
                time.sleep(15 * SLEEP)
        except Exception as e:
            last = e
        time.sleep(SLEEP * (1 + random.random() * 2 + attempt))
    raise RuntimeError(f"échec après {tries} essais : {path} ({last})")


def num(v):
    return round(float(v), 2) if isinstance(v, (int, float)) and v > 0 else None


def row(c):
    """Ligne compacte : [id, nom, numéro, rareté, set, image, cmTendance, cmMoy1, cmMoy7, cmMoy30, cmBas, tcgMarché, idCardmarket, idTcgplayer]"""
    s = c.get("set") or {}
    pr = c.get("pricing") or {}
    cm = pr.get("cardmarket") or {}
    # Cardmarket : version normale si elle a un prix, sinon version holo
    sfx = "" if num(cm.get("trend")) or num(cm.get("avg")) else "-holo"
    cm_trend = num(cm.get("trend" + sfx)) or num(cm.get("avg" + sfx))
    tp = pr.get("tcgplayer") or {}
    tcg, tcg_id = None, None
    variants = [k for k in TCG_ORDER if isinstance(tp.get(k), dict)] + \
               [k for k, v in tp.items() if isinstance(v, dict) and k not in TCG_ORDER]
    for k in variants:
        m = num(tp[k].get("marketPrice")) or num(tp[k].get("midPrice"))
        if m:
            tcg, tcg_id = m, tp[k].get("productId")
            break
    img = c.get("image") or ""
    if img.startswith(IMG_BASE):
        img = img[len(IMG_BASE):]
    return [c["id"], c.get("name"), c.get("localId"), c.get("rarity") or "—", s.get("id"), img,
            cm_trend, num(cm.get("avg1" + sfx)), num(cm.get("avg7" + sfx)), num(cm.get("avg30" + sfx)),
            num(cm.get("low" + sfx)), tcg, cm.get("idProduct"), tcg_id]


# ---------------------------------------------------------------- état précédent
state_path = os.path.join(out, "history.json.gz")
fresh_state = {"source": "tcgdex", "ids": [], "snaps": [], "lastFull": 0, "rows": {}, "sets": {}, "pending": []}
state = dict(fresh_state)
if os.path.exists(state_path):
    try:
        with gzip.open(state_path, "rt", encoding="utf-8") as f:
            old = json.load(f)
        if old.get("source") == "tcgdex":
            state.update(old)
            log(f"état précédent : {len(state['rows'])} cartes, {len(state['snaps'])} relevés, {len(state['pending'])} en attente")
        else:
            log("ancien format (API Pokémon TCG, prix figés) : on repart de zéro avec TCGdex")
    except Exception as e:
        log("état précédent illisible, on repart de zéro :", e)
rows, set_info = state["rows"], state["sets"]

# ---------------------------------------------------------------- extensions
sets = get("/sets") or []
new_sets = [s["id"] for s in sets if s["id"] not in set_info]
log(f"{len(sets)} extensions, {len(new_sets)} à détailler")


def set_detail(sid):
    d = get("/sets/" + urllib.parse.quote(sid)) or {}
    return sid, {"name": d.get("name"), "series": (d.get("serie") or {}).get("name"),
                 "date": d.get("releaseDate") or "", "total": (d.get("cardCount") or {}).get("total"),
                 "cards": [c["id"] for c in d.get("cards") or []]}


with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    for fut in as_completed([ex.submit(set_detail, sid) for sid in new_sets]):
        try:
            sid, info = fut.result()
            set_info[sid] = info
        except Exception as e:
            log("extension en échec :", e)
sets_sorted = sorted(set_info.items(), key=lambda kv: kv[1].get("date") or "", reverse=True)
sets_out = [{"id": sid, "name": i["name"], "series": i["series"], "date": i["date"], "total": i["total"]}
            for sid, i in sets_sorted]

# ---------------------------------------------------------------- cartes à relever
hour = time.gmtime(now).tm_hour
all_ids = [c["id"] for c in (get("/cards") or [])]
first_run = not rows
full_due = first_run or (hour == FULL_HOUR_UTC and now - state["lastFull"] > 12 * HOUR) or now - state["lastFull"] > 26 * HOUR
recent_ids = [cid for sid, i in sets_sorted[:3] for cid in i.get("cards", [])]
if full_due:
    todo, mode = all_ids, "complet"
else:
    todo = list(dict.fromkeys(state["pending"] + recent_ids))  # reprise d'un relevé complet interrompu + dernières sorties
    mode = "reprise + dernières extensions" if state["pending"] else "dernières extensions"
all_set = set(all_ids)
todo = [cid for cid in todo if cid in all_set] if all_ids else todo
log(f"{mode} : {len(todo)} cartes à relever (sur {len(all_ids)})")


def fetch(cid):
    if time.time() - START > BUDGET:
        return cid, "skip"
    try:
        return cid, get("/cards/" + urllib.parse.quote(cid))
    except Exception:
        return cid, "fail"


done, failed, skipped = 0, [], []
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futs = [ex.submit(fetch, cid) for cid in todo]
    for fut in as_completed(futs):
        try:
            cid, c = fut.result()
            if c == "skip":
                skipped.append(cid)
                continue
            if c == "fail":
                failed.append(cid)
                continue
            if c:
                rows[cid] = row(c)
            done += 1
            if done % 2000 == 0:
                log(f"{done}/{len(todo)} cartes")
        except Exception as e:
            log("erreur inattendue :", e)
log(f"{done} cartes relevées, {len(failed)} en échec, {len(skipped)} reportées (limite de temps)")
todo_set = set(todo)
pending_left = [cid for cid in state["pending"] if cid not in todo_set]
state["pending"] = list(dict.fromkeys(pending_left + skipped + failed))  # repris aux heures suivantes
if mode == "complet":
    state["lastFull"] = now
if all_ids:
    for cid in list(rows):  # cartes retirées de la base
        if cid not in all_set:
            rows.pop(cid)

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

latest = {"version": 2, "source": "tcgdex", "imgBase": IMG_BASE, "updated": now, "mode": mode,
          "sets": sets_out, "cards": card_rows, "refs": refs}
with open(os.path.join(out, "latest.json"), "w", encoding="utf-8") as f:
    json.dump(latest, f, ensure_ascii=False, separators=(",", ":"))

by_set = {}
for r in card_rows:
    by_set.setdefault(r[4], []).append(r[0])
ts = [s["ts"] for s in kept]
for name in os.listdir(os.path.join(out, "hist")):
    os.remove(os.path.join(out, "hist", name))
for set_id, cids in by_set.items():
    c = {cid: [[val(s, "cm", index[cid]) for s in kept], [val(s, "tcg", index[cid]) for s in kept]] for cid in cids}
    with open(os.path.join(out, "hist", f"{set_id}.json"), "w", encoding="utf-8") as f:
        json.dump({"ts": ts, "c": c}, f, separators=(",", ":"))

with gzip.open(state_path, "wt", encoding="utf-8") as f:
    json.dump(state, f, separators=(",", ":"))
log(f"terminé : {len(card_rows)} cartes, {len(kept)} relevés en historique, {len(state['pending'])} en attente")
