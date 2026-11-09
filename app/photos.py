"""Photos: what the model may do with a picture, and what only code may do.

THE MODEL (a vision model on Nebius) DESCRIBES each photo into a fixed schema and nothing else:
    object, main_colour, visible_damage (+ where, + kind), item_count, looks_like_screen, catalogue_style, legible, confidence.
It never sees the claim, never compares two photos and never says who is right.

THE CODE decides, from those descriptions and from checks that need no model at all:
    (a) buyer photo vs the shop's listing: wrong object or wrong colour supports "not as described".
    (b) buyer photo vs our own dispatch photo: intact when packed + damaged on arrival = transit damage (owed, carrier claim);
        both intact contradicts the damage claim; anything conflicting or unclear goes to review.
    (c) a perceptual hash (dHash, 64 bit, implemented here with Pillow): the buyer's photo is our own listing/dispatch image,
        or the same picture is attached to another dispute.
    (d) EXIF capture time earlier than the delivery scan (with a time-zone allowance).
A photo that is unreadable, low confidence, a screen photo or a stock catalogue shot is never a reason to refuse: it
goes to review. The consumer floor still wins: damage in a trusted buyer photo and nothing to counter it is owed.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import io
import json
import os
import re
import threading
import time
from pathlib import Path

import httpx
from PIL import Image

PHOTO_DIR = Path(__file__).resolve().parent.parent / "data" / "photos"
OBS_CACHE = PHOTO_DIR / "observations.json"
VISION_MODELS = ["nvidia/Nemotron-3.5-Super-VL", "google/gemma-3-27b-it"]

HASH_MATCH_BITS = 8  # of 64: re-saved / resized copies land at 0-4, unrelated pictures near 32
EXIF_SLACK = dt.timedelta(hours=14)  # EXIF has no time zone; allow the widest zone offset before calling it "earlier"

DESCRIBE_SYSTEM = (
    "You describe ONE photo of a household item for a returns desk. Describe only what is visible. You are not told "
    "about any dispute and you must not guess who is right. Return ONLY JSON with exactly these keys:\n"
    '{"object": <the main item, one lowercase noun such as mug, cup, plate, vase, teapot, bowl, other>,\n'
    ' "main_colour": <one common colour word for the main item: red, orange, yellow, green, blue, purple, pink, brown, '
    "white, grey, black, beige, multicolour>,\n"
    ' "visible_damage": <true if the item is cracked, chipped, broken or in pieces, else false>,\n'
    ' "damage_where": <rim, side, base, handle, whole item, or null>,\n'
    ' "damage_kind": <crack, chip, shattered, stain, other, or null>,\n'
    ' "item_count": <integer number of separate items of that kind in view>,\n'
    ' "looks_like_screen": <true if this looks like a photo of a phone or computer screen>,\n'
    ' "catalogue_style": <true if it is a professional product or museum shot on a plain backdrop>,\n'
    ' "legible": <true if the photo is sharp and clear enough to judge the condition of the item>,\n'
    ' "confidence": <high, medium or low: how sure you are of this description>}'
)

OBJECT_GROUPS = [
    {"mug", "cup", "teacup", "tumbler"},
    {"plate", "dish", "saucer", "platter"},
    {"vase", "jar", "urn", "pitcher", "jug", "vessel"},
    {"teapot", "pot", "kettle", "coffee pot", "coffeepot", "carafe"},
    {"bowl"},
    {"coffee set", "tea set", "set", "service"},
]

COLOUR_FAMILIES = {
    "red": {"red", "crimson", "scarlet", "maroon", "burgundy", "rust"},
    "orange": {"orange", "coral", "terracotta", "amber"},
    "yellow": {"yellow", "gold", "mustard", "golden"},
    "green": {"green", "sage", "olive", "mint", "lime", "emerald"},
    "blue": {"blue", "navy", "cobalt", "turquoise", "teal", "cyan", "indigo", "denim"},
    "purple": {"purple", "violet", "lilac", "lavender", "plum"},
    "pink": {"pink", "rose", "magenta", "blush"},
    "brown": {"brown", "tan", "chocolate", "bronze", "copper", "wood"},
    "light": {"white", "cream", "ivory", "beige", "off-white", "offwhite", "clear", "transparent", "silver"},
    "grey": {"grey", "gray", "charcoal", "slate"},
    "black": {"black"},
}
NEAR_FAMILIES = {frozenset(p) for p in [("black", "grey"), ("black", "blue"), ("light", "grey"), ("red", "orange"), ("red", "pink"),
                                         ("orange", "yellow"), ("orange", "brown"), ("yellow", "brown"), ("green", "blue"),
                                         ("brown", "red"), ("light", "yellow"), ("black", "brown"), ("purple", "blue"), ("purple", "pink")]}


# ---- pure image helpers -----------------------------------------------------------------------------------------
def resolve(ref: str) -> Path:
    p = Path(ref)
    return p if p.is_absolute() else PHOTO_DIR / ref


def load(ref: str) -> bytes:
    return resolve(ref).read_bytes()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:20]


def dhash(data: bytes) -> int:
    """64-bit difference hash: greyscale, 9x8, one bit per left-right brightness step. Survives resizing and re-saving."""
    im = Image.open(io.BytesIO(data)).convert("L").resize((9, 8), Image.LANCZOS)
    px = list(im.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def exif_taken(data: bytes) -> dt.datetime | None:
    """Capture time from EXIF (DateTimeOriginal, else DateTime), or None. Naive local time: EXIF carries no zone."""
    try:
        ex = Image.open(io.BytesIO(data)).getexif()
        raw = ex.get_ifd(0x8769).get(36867) or ex.get(306)
        if not raw:
            return None
        raw = str(raw).strip()
        try:
            return dt.datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
        except ValueError:
            return dt.datetime.fromisoformat(raw)
    except Exception:  # noqa: BLE001
        return None


def _parse_iso(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d.replace(tzinfo=None) if d.tzinfo is None else d.astimezone(dt.timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None


# ---- the model: describe only --------------------------------------------------------------------------------------
def clean_obs(raw: dict | None) -> dict:
    """Coerce the model's JSON to the fixed schema. Anything missing or odd lowers the confidence, it never raises."""
    raw = raw if isinstance(raw, dict) else {}

    def b(k):
        v = raw.get(k)
        return v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes")

    def s(k):
        v = raw.get(k)
        return None if v in (None, "", "null", "none") else str(v).strip().lower()

    try:
        count = max(0, int(raw.get("item_count")))
    except (TypeError, ValueError):
        count = None
    conf = s("confidence") if s("confidence") in ("high", "medium", "low") else "low"
    obj = s("object")
    if obj is None or "main_colour" not in raw:
        conf = "low"
    return {"object": obj, "main_colour": s("main_colour"), "visible_damage": b("visible_damage"), "damage_where": s("damage_where"),
            "damage_kind": s("damage_kind"), "item_count": count, "looks_like_screen": b("looks_like_screen"),
            "catalogue_style": b("catalogue_style"), "legible": b("legible") if "legible" in raw else False, "confidence": conf}


class Vision:
    """Calls the vision model through the same Nebius-compatible endpoint as the splitter. Observations are cached by
    image hash in data/photos/observations.json, so a photo is described once."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, models: list[str] | None = None, cache_path: Path | None = None):
        self.base_url = (base_url or os.environ.get("NEBIUS_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("NEBIUS_API_KEY", "")
        self.models = models or VISION_MODELS
        self.cache_path = cache_path or OBS_CACHE
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "failures": 0, "cache_hits": 0}
        self.lock = threading.Lock()
        try:
            self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}

    def _save(self):
        try:
            self.cache_path.write_text(json.dumps(self.cache, indent=1, sort_keys=True), encoding="utf-8")
        except OSError:
            pass

    def _ask(self, model: str, data: bytes, mime: str) -> dict:
        uri = f"data:{mime};base64," + base64.b64encode(data).decode()
        body = {"model": model, "temperature": 0, "max_tokens": 900, "stream": False, "messages": [
            {"role": "system", "content": DESCRIBE_SYSTEM},
            {"role": "user", "content": [{"type": "text", "text": "Describe this photo as the JSON object."}, {"type": "image_url", "image_url": {"url": uri}}]}]}
        last = None
        for attempt in range(3):
            try:
                r = httpx.post(self.base_url + "/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"}, json=body, timeout=150)
            except httpx.HTTPError as e:
                last = e
                time.sleep(1 + attempt)
                continue
            if r.status_code == 402:
                raise RuntimeError("proxy refused: spend cap reached (402)")
            if r.status_code >= 500 or r.status_code == 429:
                last = RuntimeError(f"{r.status_code}: {r.text[:160]}")
                time.sleep(2 + 2 * attempt)
                continue
            if r.status_code != 200:
                raise RuntimeError(f"{r.status_code}: {r.text[:160]}")
            j = r.json()
            u = j.get("usage") or {}
            with self.lock:
                self.usage["calls"] += 1
                self.usage["prompt_tokens"] += u.get("prompt_tokens", 0) or 0
                self.usage["completion_tokens"] += u.get("completion_tokens", 0) or 0
            text = j["choices"][0]["message"].get("content") or ""
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
            a, z = text.find("{"), text.rfind("}")
            if a < 0 or z <= a:
                raise RuntimeError("no JSON in vision output")
            return json.loads(text[a : z + 1])
        raise RuntimeError(f"vision call failed: {last}")

    def describe(self, data: bytes) -> dict:
        key = sha(data)
        with self.lock:
            hit = self.cache.get(key)
        if hit:
            self.usage["cache_hits"] += 1
            return hit
        if not self.base_url:
            return {**clean_obs(None), "error": "NEBIUS_BASE_URL not set"}
        mime = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
        err = None
        for model in self.models:
            try:
                obs = clean_obs(self._ask(model, data, mime))
                obs["model"] = model
                with self.lock:
                    self.cache[key] = obs
                    self._save()
                return obs
            except RuntimeError as e:
                err = str(e)
                if "402" in err:
                    break
            except (ValueError, KeyError) as e:
                err = f"bad vision output: {e}"
        self.usage["failures"] += 1
        return {**clean_obs(None), "error": err}


def observe(ev: dict, vision: Vision | None) -> None:
    """Fill ev['condition']['obs'][role] for every photo role that has a file and no observation yet."""
    cond = ev.get("condition") or {}
    photos = cond.get("photos") or {}
    if not photos:
        return
    obs = cond.setdefault("obs", {})
    for role, ref in photos.items():
        if role in obs or not ref:
            continue
        try:
            data = load(ref)
        except OSError:
            obs[role] = {**clean_obs(None), "error": "photo file not found"}
            continue
        obs[role] = vision.describe(data) if vision else {**clean_obs(None), "error": "no vision model configured"}


# ---- deterministic checks --------------------------------------------------------------------------------------------
def object_group(word: str | None) -> int | None:
    if not word:
        return None
    w = word.lower().strip()
    for i, g in enumerate(OBJECT_GROUPS):
        if w in g or any(re.search(rf"\b{re.escape(x)}\b", w) for x in g if len(x) > 3):
            return i
    return None


def colour_family(word: str | None) -> str | None:
    if not word:
        return None
    w = word.lower()
    for fam, words in COLOUR_FAMILIES.items():
        if any(re.search(rf"\b{re.escape(x)}\b", w) for x in words):
            return fam
    return None


def colours_differ(a: str | None, b: str | None) -> bool:
    fa, fb = colour_family(a), colour_family(b)
    return bool(fa and fb and fa != fb and frozenset((fa, fb)) not in NEAR_FAMILIES)


def _listing_expectation(ev: dict, listing_obs: dict | None) -> tuple[str | None, str | None]:
    """What the listing says the item is: object and colour. The shop's own item name first, then the listing photo."""
    names = " ".join(i.get("name", "") for i in (ev.get("purchase_record") or {}).get("items", []))
    obj = next((w for g in OBJECT_GROUPS for w in sorted(g, key=len, reverse=True) if re.search(rf"\b{re.escape(w)}s?\b", names, re.I) and w != "set"), None)
    colour = None
    for fam, words in COLOUR_FAMILIES.items():
        hit = next((w for w in words if re.search(rf"\b{re.escape(w)}\b", names, re.I) and w not in ("clear", "wood", "silver", "gold", "rose", "amber", "rust")), None)
        if hit:
            colour = hit
            break
    if listing_obs and not listing_obs.get("error"):
        obj = obj or (listing_obs.get("object") if object_group(listing_obs.get("object")) is not None else None)
        colour = colour or listing_obs.get("main_colour")
    return obj, colour


def trusted(o: dict | None) -> bool:
    return bool(o) and not o.get("error") and o.get("legible") is True and o.get("confidence") in ("high", "medium")


def _delivered_dt(ev: dict) -> dt.datetime | None:
    scans = (ev.get("shipment") or {}).get("scans") or []
    d = [x for x in scans if x.get("status") == "DELIVERED" and x.get("at")]
    return _parse_iso(d[-1]["at"]) if d else None


def _first_scan_dt(ev: dict) -> dt.datetime | None:
    scans = [x for x in (ev.get("shipment") or {}).get("scans") or [] if x.get("at")]
    return min((_parse_iso(x["at"]) for x in scans if _parse_iso(x["at"])), default=None)


def has_real_photos(ev: dict) -> bool:
    c = ev.get("condition") or {}
    return "photos" in c


def describe_obs(o: dict | None) -> str:
    if not o:
        return "no description"
    if o.get("error"):
        return "could not be described (" + str(o["error"])[:60] + ")"
    bits = [f"{o.get('main_colour') or '?'} {o.get('object') or '?'}"]
    bits.append("damage visible" + (f" ({o['damage_kind']}" + (f", {o['damage_where']}" if o.get("damage_where") else "") + ")" if o.get("damage_kind") else "") if o.get("visible_damage") else "no damage visible")
    if o.get("looks_like_screen"):
        bits.append("looks like a screen")
    if o.get("catalogue_style"):
        bits.append("catalogue-style shot")
    if not o.get("legible"):
        bits.append("not legible")
    bits.append(f"confidence {o.get('confidence')}")
    return ", ".join(bits)


def assess(ev: dict) -> dict:
    """Run the four checks on the evidence's photos and return the verdict for the 'not as described' statement.

    Pure code over the stored observations: no model call happens here, so the same input always gives the same answer.
    """
    cond = ev.get("condition") or {}
    refs = cond.get("photos") or {}
    obs = cond.get("obs") or {}
    info: dict[str, dict] = {}
    for role in ("listing", "outbound", "buyer"):
        ref = refs.get(role)
        if not ref:
            continue
        entry = {"file": ref, "url": None, "obs": obs.get(role), "dhash": None, "exif_at": None}
        if not Path(ref).is_absolute():
            entry["url"] = "/photos/" + ref.replace("\\", "/")
        try:
            data = load(ref)
            entry["dhash"] = f"{dhash(data):016x}"
            ex = exif_taken(data)
            entry["exif_at"] = ex.isoformat(timespec="minutes") if ex else None
            entry["sha"] = sha(data)
        except Exception as e:  # noqa: BLE001
            entry["error"] = f"{type(e).__name__}: {e}"
        info[role] = entry
    checks: list[dict] = []

    def chk(cid, title, status, text):
        checks.append({"id": cid, "title": title, "status": status, "text": text})

    def done(verdict, strength, reason, at_fault=False, **extra):
        return {"verdict": verdict, "strength": strength, "reason": reason, "at_fault": at_fault, "photos": info, "checks": checks, **extra}

    buyer, ours, lst = info.get("buyer"), info.get("outbound"), info.get("listing")
    bo, oo, lo = (buyer or {}).get("obs"), (ours or {}).get("obs"), (lst or {}).get("obs")
    if buyer:
        chk("buyer_seen", "Buyer photo (model's description)", "info", describe_obs(bo))
    if ours:
        chk("dispatch_seen", "Our dispatch photo (model's description)", "info", describe_obs(oo))

    # ---- dispatch photo sanity: it must have been taken before the carrier took the parcel ----
    ours_ok = bool(ours) and trusted(oo)
    if ours:
        taken, first = _parse_iso(cond.get("outbound_taken_at")), _first_scan_dt(ev)
        if taken and first and taken > first:
            ours_ok = False
            chk("dispatch_time", "Dispatch photo timing", "flag", f"Our photo is stamped {taken:%d %b %H:%M}, after the carrier's first scan ({first:%d %b %H:%M}); it cannot show the item as packed.")
        elif not trusted(oo):
            chk("dispatch_quality", "Dispatch photo quality", "flag", "Our own dispatch photo could not be read with enough confidence.")

    if not buyer:
        if ours and not ours_ok:
            return done("unverifiable", "weak", "Our dispatch photo cannot be relied on and the buyer sent none. A person has to look.")
        if ours and oo.get("visible_damage"):
            chk("dispatch_damage", "Dispatch photo shows damage", "supports", "Our own packing photo already shows damage.")
            return done("true", "strong", "Our own dispatch photo shows the item damaged before it left; nothing to counter the claim.", True)
        if ours:
            return done("false", "medium", "Our dispatch photo shows the item intact and the buyer sent no photo of any damage.")
        return done("unverifiable", "weak", "Code cannot inspect the goods; condition claims need photos or a return.", note="no photos")

    # ---- (c) is the buyer's picture really the buyer's? ----
    try:
        bh = int(buyer["dhash"], 16)
    except (TypeError, ValueError):
        bh = None
    if bh is not None:
        for label, other in (("our listing photo", lst), ("our dispatch photo", ours)):
            if other and other.get("dhash") and hamming(bh, int(other["dhash"], 16)) <= HASH_MATCH_BITS:
                d = hamming(bh, int(other["dhash"], 16))
                chk("c_own_image", "Is the picture the buyer's own?", "contradicts", f"The attachment matches {label} (hash distance {d} of 64): it is the shop's own image, re-saved, so it cannot show the parcel the buyer received.")
                return done("false", "medium", f"The photo the buyer sent is {label}, not a photo of the delivered item.", own_image=True)
        for od in cond.get("other_disputes") or []:
            try:
                oh = int(dhash(load(od["photo"])))
            except Exception:  # noqa: BLE001
                continue
            if hamming(bh, oh) <= HASH_MATCH_BITS:
                chk("c_reused", "Is the picture the buyer's own?", "flag", f"The same picture (hash distance {hamming(bh, oh)} of 64) is attached to another dispute, order {od.get('order', '?')}.")
                return done("unverifiable", "weak", f"The buyer's photo is also attached to order {od.get('order', '?')}'s dispute. A person has to compare them.", reused=True)
        chk("c_own_image", "Is the picture the buyer's own?", "ok", "Not a copy of our listing or dispatch photo, and not on another dispute.")

    # ---- (d) capture time vs delivery ----
    exif, delivered = _parse_iso(buyer.get("exif_at")), _delivered_dt(ev)
    if exif and delivered and exif < delivered - EXIF_SLACK:
        chk("d_exif", "Photo capture time", "flag", f"Metadata says the photo was taken {exif:%d %b %Y %H:%M}, before the parcel was scanned delivered ({delivered:%d %b %Y %H:%M}). It cannot show the delivered item; clocks can be wrong, so a person decides.")
        return done("unverifiable", "weak", "The buyer's photo is stamped earlier than the delivery. A person has to look.", exif_early=True)
    chk("d_exif", "Photo capture time", "ok" if exif else "info",
        "No capture time in the file (most apps strip it); not held against the buyer." if not exif else f"Taken {exif:%d %b %Y %H:%M}, after the delivery scan.")

    # ---- can the model's description be used at all? ----
    if bo and not bo.get("error") and (bo.get("looks_like_screen") or bo.get("catalogue_style")):
        chk("q_trust", "Is it a photo of the item?", "flag", "The photo looks like a screen or a studio catalogue shot rather than a picture of the parcel received.")
        return done("unverifiable", "weak", "The buyer's attachment looks like a stock or screen image, not their own photo. A person has to look.")
    if not trusted(bo):
        chk("q_trust", "Is the photo readable?", "flag", "The photo is too unclear or the description too uncertain to rely on.")
        return done("unverifiable", "weak", "The buyer's photo could not be read with enough confidence. A person has to look.")

    # ---- (a) buyer photo vs the listing ----
    want_obj, want_colour = _listing_expectation(ev, lo)
    og_b, og_w = object_group(bo.get("object")), object_group(want_obj)
    if og_b is not None and og_w is not None and og_b != og_w:
        chk("a_listing", "Buyer photo vs listing", "supports", f"The listing is a {want_obj}; the buyer's photo shows a {bo.get('object')}.")
        return done("true", "medium", f"The buyer's photo shows a {bo.get('object')} but the order is a {want_obj}.", True, item_mismatch=True)
    if colours_differ(bo.get("main_colour"), want_colour):
        chk("a_listing", "Buyer photo vs listing", "supports", f"The order is {want_colour}; the buyer's photo shows {bo.get('main_colour')}.")
        return done("true", "medium", f"The order is {want_colour} but the buyer's photo shows {bo.get('main_colour')}.", True, colour_mismatch=True)
    chk("a_listing", "Buyer photo vs listing", "ok", f"Object and colour are consistent with the listing ({want_colour or 'colour not listed'} {want_obj or 'item'}).")

    # ---- (b) buyer photo vs our dispatch photo ----
    damaged = bool(bo.get("visible_damage"))
    if not ours:
        if damaged:
            chk("b_dispatch", "Buyer photo vs our dispatch photo", "supports", "The buyer's photo shows damage and the shop has no dispatch photo to counter it.")
            return done("true", "strong", "The buyer's photo shows the damage and the shop has no dispatch photo to counter it.", True)
        chk("b_dispatch", "Buyer photo vs our dispatch photo", "info", "The buyer's photo shows no damage and the shop has no dispatch photo.")
        return done("unverifiable", "weak", "The buyer's photo does not show the damage claimed and there is no dispatch photo. A person has to look.")
    if not ours_ok:
        return done("unverifiable", "weak", "The dispatch photo cannot be relied on, so the two photos cannot be compared. A person has to look.")
    if oo.get("visible_damage"):
        chk("b_dispatch", "Buyer photo vs our dispatch photo", "supports", "Our own dispatch photo already shows damage: we sent it that way.")
        return done("true", "strong", "Our dispatch photo shows damage already; the claim holds.", True)
    same_item = not (object_group(oo.get("object")) is not None and og_b is not None and object_group(oo.get("object")) != og_b) and not colours_differ(oo.get("main_colour"), bo.get("main_colour"))
    if damaged and same_item:
        chk("b_dispatch", "Buyer photo vs our dispatch photo", "supports", "Intact when we packed it, damaged when the buyer received it: damage in transit.")
        return done("true", "strong", "Transit damage: our dispatch photo shows the item intact, the buyer's photo shows it damaged on arrival. A remedy is owed and the carrier is liable.", True, carrier_claim=True)
    if damaged:
        chk("b_dispatch", "Buyer photo vs our dispatch photo", "flag", "The buyer's photo and our dispatch photo do not look like the same item, so the comparison cannot be trusted.")
        return done("unverifiable", "weak", "The two photos do not look like the same item. A person has to look.")
    if same_item:
        chk("b_dispatch", "Buyer photo vs our dispatch photo", "contradicts", "Both our dispatch photo and the buyer's photo show the item intact.")
        return done("false", "medium", "The buyer's own photo and our dispatch photo both show the item intact; the damage claim is contradicted.")
    chk("b_dispatch", "Buyer photo vs our dispatch photo", "flag", "Neither photo shows damage, but they do not look like the same item.")
    return done("unverifiable", "weak", "Neither photo shows damage but they differ. A person has to look.")
