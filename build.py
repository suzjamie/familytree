#!/usr/bin/env python3
"""Family tree -> static website.

Reads a GEDCOM export (e.g. from Family Tree Maker) plus a folder of images and
writes a complete static site into the output folder (default: docs/), ready for
GitHub Pages. Images are matched to people by file name, using the media links
stored in the GEDCOM. Only Python 3.8+ is required; Pillow is optional but
strongly recommended (it resizes images so the repo stays small).

    python build.py                       # uses site.json
    python build.py --gedcom my.ged --media ~/Pictures/tree --out docs
"""
from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import html
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, unquote

try:
    from PIL import Image, ImageOps
except ImportError:  # works without Pillow, but images are copied unresized
    Image = None

esc = html.escape

DEFAULTS = {
    "site_title": "The Family Archive",
    "tagline": "Our ancestors, their stories and their photographs",
    "intro": "Welcome. This site collects the people, places and photographs from my family research.",
    "footer_note": "Corrections and additions are welcome.",
    "contact_email": "",
    "domain": "",
    "gedcom": "tree.ged",         # one file, or several: ["paternal.ged", "maternal.ged"]
    "media_dir": "media",
    "output_dir": "docs",
    "home_person": "",
    "pedigree_generations": 4,
    "descendant_generations": 3,
    "web_image_px": 1600,
    "thumb_px": 480,
    "max_images_per_person": 0,
    "exclude_images": [],
    "hide_living": False,
    "grandparents": ["", "", "Robert Weiss", "Rose Hecht"],  # left to right; a name or GEDCOM id, "" = blank
    "tags_file": "tags.json",     # face tags made with tagger.html (optional)
    "new_tab_links": "external",  # "external" = photos + outside web links; "all" = every link
    "copyright_text": "",         # name shown after the © in the footer (empty = site title)
    "header_image": "",           # optional wide picture behind the title (2400 x 240 px works well)
    "webmaster_message": "After doing genealogy for 30+ years, it has become my life’s work.\nContact me via the link below with questions or information to share.\n... Suzanne",  # shown in the header, to the right of the site title; leave empty to hide it
    "logo": "",                   # optional path to your own logo image (leave empty for the built-in tree)
    "header_links": [],           # optional stacked links in the top-right of the header, as {"label": "...", "url": "..."}
}

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp"}

EVENT_LABELS = {
    "BIRT": "Born", "CHR": "Christened", "BAPM": "Baptized", "DEAT": "Died",
    "BURI": "Buried", "CREM": "Cremated", "RESI": "Lived", "OCCU": "Occupation",
    "EDUC": "Education", "GRAD": "Graduated", "EMIG": "Emigrated",
    "IMMI": "Immigrated", "NATU": "Naturalized", "CENS": "Census",
    "MILI": "Military Service", "RETI": "Retired", "PROB": "Probate",
    "WILL": "Will", "RELI": "Religion", "EVEN": "Event", "MARR": "Married",
    "DIV": "Divorced",
}
MONTHS = {m.upper(): m for m in
          "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()}
DATE_WORDS = {"ABT": "about", "EST": "est.", "CAL": "calc.", "BEF": "before",
              "AFT": "after", "BET": "between", "AND": "and", "FROM": "from",
              "TO": "to"}


# --------------------------------------------------------------------------
# GEDCOM parsing
# --------------------------------------------------------------------------
class Node:
    __slots__ = ("tag", "value", "xref", "kids")

    def __init__(self, tag, value="", xref=None):
        self.tag, self.value, self.xref, self.kids = tag, value, xref, []

    def first(self, tag):
        for k in self.kids:
            if k.tag == tag:
                return k
        return None

    def all(self, tag):
        return [k for k in self.kids if k.tag == tag]

    def val(self, path, default=""):
        node = self
        for part in path.split("/"):
            node = node.first(part) if node else None
        return node.value.strip() if node and node.value else default


LINE = re.compile(r"^\s*(\d+)\s+(?:(@[^@]+@)\s+)?(\S+)(?:\s(.*))?$")


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1")


XREF = re.compile(r"@([^@\s]+)@")


def parse_gedcom(paths):
    """Read one GEDCOM file, or several. With several, every id after the first file gets a
    suffix (@I1@ -> @I1_2@) so the trees never clash, and all people end up in one site."""
    if isinstance(paths, (str, Path)):
        paths = [paths]
    roots = []
    for n, path in enumerate(paths, 1):
        text = read_text(Path(path))
        if n > 1:
            text = XREF.sub(lambda m: f"@{m[1]}_{n}@", text)
        part = _parse_text(text)
        if n > 1:
            part = [r for r in part if r.tag not in ("HEAD", "TRLR")]
        roots += part
        n_p = sum(1 for r in part if r.tag == "INDI")
        print(f"  Read {Path(path).resolve()}: {n_p} people, {sum(1 for r in part if r.tag == 'FAM')} families"
              + ("   <-- NO PEOPLE FOUND IN THIS FILE" if n_p == 0 else ""))
    return roots


def _parse_text(text):
    roots, stack = [], []
    for raw in re.split(r"\r\n|\r|\n", text):          # not str.splitlines(): it also splits on stray characters inside notes
        m = LINE.match(raw)
        if not m:
            continue
        lvl, xref, tag, val = int(m[1]), m[2], m[3].upper(), (m[4] or "")
        if tag in ("CONC", "CONT"):
            if stack:                                      # always continues the line just above, whatever level the file gives it
                stack[-1].value += ("\n" if tag == "CONT" else "") + val
            continue
        node = Node(tag, val, xref)
        del stack[lvl:]
        if lvl == 0:
            roots.append(node)
        elif stack:
            stack[lvl - 1].kids.append(node)
        stack.append(node)
    return roots


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def file_key(img):
    """Sort photos by file name, with numbers in order (photo2 before photo10), ignoring capitals."""
    name = Path(img.src.name).stem.lower()
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)] + [img.key]


def slugify(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text or "unknown"


def fmt_date(d: str) -> str:
    out = []
    toks = (d or "").split()
    first = toks[0].upper().strip("()") if toks else ""
    if first in ("BET", "BETWEEN"):
        start, end = ("BET", "BETWEEN"), ("AND",)
    elif first == "FROM":
        start, end = ("FROM",), ("TO",)
    else:
        start = end = ()
    rng = bool(start) and any(t.upper().strip("()") in end for t in toks[1:])
    for t in toks:
        u = t.upper().strip("()")
        if rng and u in start:
            continue                                   # 'BET 1890 AND 1895' / 'FROM 1890 TO 1895' -> '1890 - 1895'
        if rng and u in end:
            out.append("-")
            continue
        out.append(MONTHS.get(u) or DATE_WORDS.get(u) or t)
    return " ".join(out)


MONTH_NUM = {m.upper(): i for i, m in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}


def date_parts(d: str):
    """(year, month, day) from a GEDCOM date such as '1 JAN 1938', 'ABT 1890', 'BET 1890 AND 1895'.
    Missing parts are 0, so '1938' sorts before 'JAN 1938' before '1 JAN 1938'. None if no year."""
    d = (d or "").upper()
    for sep in (" AND ", " TO "):           # BET x AND y / FROM x TO y: sort by the first date
        if sep in d:
            left, right = d.split(sep, 1)
            if re.search(r"\b\d{3,4}\b", left):
                d = left
            else:                           # 'BET MAR AND JUL 1939': year only on the right
                end = date_parts(right)
                mon = next((MONTH_NUM[w[:3]] for w in re.findall(r"[A-Z]+", left) if w[:3] in MONTH_NUM), 0)
                if end:
                    return end[0], mon, 0
                return None
            break
    m = re.search(r"(?:\b(\d{1,2})\s+)?(?:\b([A-Z]{3})[A-Z]*\.?\s+)?\b(\d{3,4})\b", d)
    if not m:
        return None
    day = int(m.group(1)) if m.group(1) and m.group(2) in MONTH_NUM else 0
    month = MONTH_NUM.get(m.group(2) or "", 0)
    return int(m.group(3)), month, day


EARLY_TAGS = {"BIRT", "CHR", "BAPM"}
LATE_TAGS = {"DEAT", "BURI", "CREM", "PROB", "WILL"}


def event_sort_key(tag: str, parts):
    """Chronological order; undated births first, undated deaths/burials last."""
    if parts:
        return (1,) + parts
    return ((0 if tag in EARLY_TAGS else 3 if tag in LATE_TAGS else 2), 0, 0, 0)


def year_of(d: str):
    m = re.search(r"\b(\d{3,4})\b", d or "")
    return int(m.group(1)) if m else None


def year_span(d: str):
    """(start_year, end_year) from a GEDCOM date such as 'BET 1890 AND 1895' or 'FROM 1920 TO 1925'.
    end_year is None unless the date is an explicit range with its own year on each side."""
    u = (d or "").upper()
    toks = u.split()
    first = toks[0].strip("()") if toks else ""
    sep = "AND" if first in ("BET", "BETWEEN") else "TO" if first == "FROM" else None
    if sep:
        stripped = [t.strip("()") for t in toks]
        if sep in stripped:
            i = stripped.index(sep)
            y1 = year_of(" ".join(toks[1:i]))
            y2 = year_of(" ".join(toks[i + 1:]))
            if y1 and y2:
                return y1, y2
    return year_of(d), None


def clean(text: str) -> str:
    """FTM exports notes and citations with HTML tags and entities; make plain text."""
    text = re.sub(r"(?i)</?(li|p|br|ul|ol|div)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


URL_RE = re.compile(r"https?://[^\s<>\"]*[^\s<>\".,;:)\]]")


def rich(text: str) -> str:
    """Escape text and turn bare URLs into short, clickable links."""
    def link(m):
        raw = html.unescape(m[0])
        host = re.match(r"https?://([^/]+)", raw)
        label = raw if len(raw) <= 45 else (host.group(1) + "/…" if host else raw[:45] + "…")
        return f'<a href="{m[0]}" target="_blank" rel="noopener">{label}</a>'
    return URL_RE.sub(link, esc(text))


def rich_link(text: str) -> str:
    """Like rich(), but a bare URL becomes just the word "Link" rather than showing the address."""
    def link(m):
        return f'<a href="{m[0]}" target="_blank" rel="noopener">Link</a>'
    return URL_RE.sub(link, esc(text))


def paras(text: str) -> str:
    chunks = [c.strip() for c in re.split(r"\n\s*\n|\n", text) if c.strip()]
    return "".join(f"<p>{rich(c)}</p>" for c in chunks)


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------
class Img:
    def __init__(self, key, src, title):
        self.key, self.src, self.title = key, src, title
        self.name = ""
        self.people = []  # Person objects, in order of attachment
        self.tag_only = []  # people added to the photo only by a face tag (not linked in the tree)
        self.tags = []    # (Person, x, y, r) face tags, as fractions of the image width


class Person:
    def __init__(self, rec: Node):
        self.id = rec.xref
        self.slug = slugify(rec.xref.strip("@"))
        self.rec = rec
        given = rec.val("NAME/GIVN")
        sur = rec.val("NAME/SURN")
        if not given and not sur:
            raw = rec.val("NAME")
            m = re.match(r"^\s*([^/]*?)\s*/([^/]*)/\s*(.*)$", raw)
            if m:
                given, sur = m[1], m[2]
            else:
                given = raw
        self.given, self.surname = given.strip(), sur.strip()
        self.sex = rec.val("SEX").upper()
        self.events, self.notes, self.sources = [], [], []
        self.media = []        # (Img, is_primary, from_family)
        self.famc, self.fams = [], []
        self.hidden = False

    @property
    def name(self):
        if self.hidden:
            return "Living"
        n = f"{self.given} {self.surname}".strip()
        return n or "Unnamed"

    def event(self, tag):
        return next((e for e in self.events if e["tag"] == tag), None)

    @property
    def birth_year(self):
        e = self.event("BIRT") or self.event("CHR") or self.event("BAPM")
        return e["year"] if e else None

    @property
    def birth_sort(self):
        e = self.event("BIRT") or self.event("CHR") or self.event("BAPM")
        return e["sort"] if e else (9, 0, 0, 0)

    @property
    def death_year(self):
        e = self.event("DEAT") or self.event("BURI")
        return e["year"] if e else None

    @property
    def lifespan(self):
        b, d = self.birth_year, self.death_year
        if b and d:
            return f"{b}–{d}"
        return f"b. {b}" if b else (f"d. {d}" if d else "")

    @property
    def portrait(self):
        own = [m for m in self.media if not m[2]]
        for img, prim, _ in own:
            if prim:
                return img
        return own[0][0] if own else None


class Family:
    def __init__(self, rec: Node):
        self.id = rec.xref
        self.rec = rec
        self.husb = rec.val("HUSB")
        self.wife = rec.val("WIFE")
        self.chil = [c.value.strip() for c in rec.all("CHIL")]
        self.events = []
        self.media = []


class Tree:
    def __init__(self, roots, cfg, media_index, report):
        self.cfg, self.report = cfg, report
        self.index = media_index
        self.notes_rec = {r.xref: r for r in roots if r.tag == "NOTE"}
        self.sour_rec = {r.xref: r for r in roots if r.tag == "SOUR"}
        self.obje_rec = {r.xref: r for r in roots if r.tag == "OBJE"}
        self.images = {}
        self.people = {r.xref: Person(r) for r in roots if r.tag == "INDI"}
        self.families = {r.xref: Family(r) for r in roots if r.tag == "FAM"}

        for p in self.people.values():
            self._fill_person(p)
        for f in self.families.values():
            self._fill_family(f)
        self._mark_living()
        self._apply_exclusions()

    # -- text, sources ----------------------------------------------------
    def note_text(self, node):
        out = []
        for n in node.all("NOTE"):
            if n.value.startswith("@"):
                rec = self.notes_rec.get(n.value.strip())
                if rec and rec.value.strip():
                    out.append(rec.value.strip())
            elif n.value.strip():
                out.append(n.value.strip())
        return clean("\n\n".join(out))

    def cites(self, node):
        out = []
        for s in node.all("SOUR"):
            if s.value.startswith("@"):
                rec = self.sour_rec.get(s.value.strip())
                title = (rec.val("TITL") or rec.val("ABBR")) if rec else ""
            else:
                title = s.value.strip()
            title = clean(title)
            if title:
                out.append((title, clean(s.val("PAGE"))))
        return out

    def event_from(self, node):
        tag = node.tag
        label = EVENT_LABELS[tag]
        if tag == "EVEN" and node.val("TYPE"):
            label = " ".join(w[:1].upper() + w[1:] for w in node.val("TYPE").split())
        text = clean(node.value)                   # the event's own "Description" field, not its Note
        if text.strip().lower() in ("y", "n", "yes", "no"):    # e.g. "1 DEAT Y" just flags that it happened
            text = ""
        date = node.val("DATE")
        media, seen = [], set()
        for holder in [node] + node.all("SOUR"):          # the event itself + its citations
            for img, _, _ in self.media_from(holder):
                if img.key not in seen:
                    seen.add(img.key)
                    media.append(img)
        year, year_end = year_span(date)
        return {"tag": tag, "label": label, "date": fmt_date(date),
                "sort": event_sort_key(tag, date_parts(date)),
                "year": year, "year_end": year_end, "place": node.val("PLAC"), "text": text,
                "note": self.note_text(node), "cites": self.cites(node), "media": media}

    # -- media ------------------------------------------------------------
    def media_from(self, node, from_family=False):
        out = []
        for o in node.all("OBJE") + node.all("_PHOTO"):
            rec = self.obje_rec.get(o.value.strip()) if o.value.startswith("@") else o
            if rec is None:
                continue
            prim = o.tag == "_PHOTO" or "Y" in (o.val("_PRIM").upper() + rec.val("_PRIM").upper())
            for f in rec.all("FILE"):
                raw = unquote(f.value.strip())
                base = re.split(r"[\\/]", raw)[-1]
                if not base:
                    continue
                ext = Path(base).suffix.lower()
                if ext not in IMAGE_EXT:
                    continue  # PDFs and other non-image files are ignored
                key = base.lower()
                src = self.index.get(key)
                if src is None:
                    self.report.append(f"not found in media folder: {base}")
                    continue
                title = rec.val("TITL") or f.val("TITL") or o.val("TITL")
                img = self.images.get(key)
                if img is None:
                    img = self.images[key] = Img(key, src, title)
                elif title and not img.title:
                    img.title = title
                out.append((img, prim, from_family))
        return out

    # -- fill -------------------------------------------------------------
    def _fill_person(self, p: Person):
        rec = p.rec
        for k in rec.kids:
            if k.tag in EVENT_LABELS:
                p.events.append(self.event_from(k))
        p.events.sort(key=lambda e: e["sort"])
        for e in p.events:
            for img in e["media"]:
                p.media.append((img, False, "event"))     # "event": never used as the portrait
        p.notes = [t for t in [self.note_text(rec)] if t]
        seen = set()
        for node in [rec] + [k for k in rec.kids if k.tag in EVENT_LABELS]:
            for c in self.cites(node):
                if c not in seen:
                    seen.add(c)
                    p.sources.append(c)
        p.famc = [c.value.strip() for c in rec.all("FAMC")]
        p.fams = [c.value.strip() for c in rec.all("FAMS")]
        for item in self.media_from(rec):
            p.media.append(item)

    def _fill_family(self, f: Family):
        for k in f.rec.kids:
            if k.tag in ("MARR", "DIV"):
                f.events.append(self.event_from(k))
        f.media = self.media_from(f.rec, from_family=True)
        for e in f.events:
            for img in e["media"]:
                f.media.append((img, False, True))
        # marriage events also appear on each spouse's timeline
        for pid in (f.husb, f.wife):
            p = self.people.get(pid)
            if p:
                for e in f.events:
                    other = self.people.get(f.wife if pid == f.husb else f.husb)
                    ev = dict(e)
                    ev["text"] = ""
                    ev["spouse"] = other
                    p.events.append(ev)
                p.events.sort(key=lambda e: e["sort"])
                for item in f.media:
                    p.media.append(item)

    # -- privacy / curation ----------------------------------------------
    def _mark_living(self):
        if not self.cfg["hide_living"]:
            return
        this_year = datetime.date.today().year
        for p in self.people.values():
            if p.event("DEAT") or p.event("BURI"):
                continue
            by = p.birth_year
            if by and this_year - by < 110:
                p.hidden = True
                p.events, p.notes, p.sources, p.media = [], [], [], []

    def _apply_exclusions(self):
        excl = {x.lower() for x in self.cfg["exclude_images"]}
        cap = int(self.cfg["max_images_per_person"] or 0)
        for p in self.people.values():
            items = [m for m in p.media if m[0].key not in excl]
            if cap and len(items) > cap:
                items.sort(key=lambda m: not m[1])  # primary first, stable
                items = items[:cap]
            p.media = items
        used = set()
        for p in self.people.values():
            for img, _, _ in p.media:
                if p not in img.people:
                    img.people.append(p)
                used.add(img.key)
        self.images = {k: v for k, v in self.images.items() if k in used}

    # -- relations --------------------------------------------------------
    def parents(self, p: Person):
        for fid in p.famc:
            f = self.families.get(fid)
            if f:
                return self.people.get(f.husb), self.people.get(f.wife)
        return None, None

    def unions(self, p: Person):
        out = []
        for fid in p.fams:
            f = self.families.get(fid)
            if not f:
                continue
            sid = f.wife if f.husb == p.id else f.husb
            kids = [self.people[c] for c in f.chil if c in self.people]
            kids.sort(key=lambda c: c.birth_sort)
            marr = next((e for e in f.events if e["tag"] == "MARR"), None)
            div = next((e for e in f.events if e["tag"] == "DIV"), None)
            out.append({"spouse": self.people.get(sid), "marr": marr, "div": div, "kids": kids})
        return out


# --------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------
def build_media_index(media_dirs):
    if isinstance(media_dirs, (str, Path)):
        media_dirs = [media_dirs]
    idx = {}
    for media_dir in media_dirs:
        if Path(media_dir).is_dir():
            for p in sorted(Path(media_dir).rglob("*")):
                if p.is_file() and p.suffix.lower() in IMAGE_EXT:
                    idx.setdefault(p.name.lower(), p)
    return idx


def process_images(tree: Tree, out: Path, cfg, report):
    web_dir, thumb_dir = out / "media" / "web", out / "media" / "thumb"
    web_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir.mkdir(parents=True, exist_ok=True)
    failed = []
    for img in tree.images.values():
        h = hashlib.sha1(img.key.encode()).hexdigest()[:8]
        stem = slugify(Path(img.src.name).stem)[:60]
        ext = ".jpg" if Image else img.src.suffix.lower()
        img.name = f"{stem}-{h}{ext}"
        web, thumb = web_dir / img.name, thumb_dir / img.name
        fresh = web.exists() and thumb.exists() and \
            min(web.stat().st_mtime, thumb.stat().st_mtime) >= img.src.stat().st_mtime
        if fresh:
            continue
        if Image is None:
            shutil.copy2(img.src, web)
            shutil.copy2(img.src, thumb)
            continue
        try:
            with Image.open(img.src) as im:
                im = ImageOps.exif_transpose(im)
                if im.mode in ("RGBA", "LA", "P"):
                    im = im.convert("RGBA")
                    bg = Image.new("RGB", im.size, (255, 255, 255))
                    bg.paste(im, mask=im.split()[-1])
                    im = bg
                elif im.mode != "RGB":
                    im = im.convert("RGB")
                for dest, px, q in ((web, cfg["web_image_px"], 82), (thumb, cfg["thumb_px"], 78)):
                    copy = im.copy()
                    copy.thumbnail((px, px), Image.LANCZOS)
                    copy.save(dest, "JPEG", quality=q, optimize=True, progressive=True)
        except Exception as exc:  # unreadable or corrupt file
            report.append(f"could not read image {img.src.name}: {exc}")
            failed.append(img)
    for img in failed:
        tree.images.pop(img.key, None)
        for p in tree.people.values():
            p.media = [m for m in p.media if m[0] is not img]



def apply_tags(tree, path, report):
    """Read tags.json (made by tagger.html) and attach the face tags to photos and people."""
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        report.append(f"could not read {path.name}: {exc}")
        return 0
    byname = defaultdict(list)
    for p in tree.people.values():
        byname[p.name.lower()].append(p)
    count = 0
    for key, lst in (data.get("photos") or {}).items():
        img = tree.images.get(key.lower())
        if img is None:
            report.append(f"tags for a photo that is not in the tree: {key}")
            continue
        for t in lst:
            p = tree.people.get(t.get("id"))
            if p is None:
                cand = byname.get(str(t.get("name", "")).lower(), [])
                p = cand[0] if len(cand) == 1 else None
            if p is None:
                report.append(f"tag for someone not found in the tree: {t.get('name') or t.get('id')} ({key})")
                continue
            if p.hidden:
                continue
            try:
                x = min(1.0, max(0.0, float(t["x"])))
                y = min(1.0, max(0.0, float(t["y"])))
                r = min(0.3, max(0.008, float(t.get("r") or 0.03)))
            except (KeyError, TypeError, ValueError):
                continue
            img.tags.append((p, x, y, r))
            if p not in img.people:
                img.people.append(p)
                img.tag_only.append(p)
            if all(m[0] is not img for m in p.media):
                p.media.append((img, False, "event"))  # never becomes the portrait
            count += 1
    return count


def write_tagger(tree, cfg, out, base):
    """Write tagger.html next to site.json: a local page for clicking faces and naming them."""
    people = sorted(
        ({"id": p.id, "name": p.name, "yrs": p.lifespan} for p in tree.people.values()),
        key=lambda d: d["name"].lower())
    existing = {}
    for img in tree.images.values():
        if img.tags:
            existing[img.key] = [{"id": q.id, "name": q.name, "x": round(x, 4), "y": round(y, 4),
                                  "r": round(r, 4)} for q, x, y, r in img.tags]
    rel = Path(os.path.relpath(out, base)).as_posix()
    photos = [{"key": i.key, "name": i.name, "title": i.title, "att": [q.id for q in i.people
              if q not in i.tag_only]}
              for i in tree.images.values()]
    photos.sort(key=lambda d: (-len(d["att"]), (d["title"] or "~").lower()))
    data = {"people": people, "photos": photos, "tags": existing, "dir": rel}
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    tpl = (Path(__file__).resolve().parent / "assets" / "tagger.html").read_text(encoding="utf-8")
    (base / "tagger.html").write_text(tpl.replace("__DATA__", blob), encoding="utf-8")


def tree_svg() -> str:
    """A white tree-of-life emblem: fine branching crown, a bare trunk, and roots (original artwork, drawn in code)."""
    import math
    segs, leaves = [], []

    def grow(x, y, ang, length, width, depth, down=False):
        dx, dy = math.sin(ang), -math.cos(ang)
        x2, y2 = x + length * dx, y + length * dy
        segs.append((x, y, x2, y2, width))
        if depth == 0:
            if not down:
                leaves.append((x2, y2, ang))
            return
        spread = 0.62 if not down else 0.55
        grow(x2, y2, ang - spread, length * 0.75, width * 0.72, depth - 1, down)
        grow(x2, y2, ang + spread * 0.9, length * 0.72, width * 0.72, depth - 1, down)
        if depth >= 4 and not down:
            grow(x2, y2, ang + 0.04, length * 0.6, width * 0.7, depth - 2, down)

    base_x, base_y = 60, 98
    segs.append((base_x, base_y, base_x, base_y - 9, 4.2))      # bare trunk: the gap between crown and roots
    grow(base_x, base_y - 9, 0.0, 25, 4.2, 6)                   # trunk and crown
    grow(base_x, base_y, math.pi, 13, 3.0, 4, down=True)        # roots
    grow(base_x, base_y, math.pi - 0.7, 11, 2.2, 3, down=True)
    grow(base_x, base_y, math.pi + 0.7, 11, 2.2, 3, down=True)
    parts = [f'<line x1="{a:.1f}" y1="{b:.1f}" x2="{c:.1f}" y2="{d:.1f}" stroke-width="{w:.2f}"/>'
             for a, b, c, d, w in segs]
    parts += [f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="1.5" ry="2.5" '
              f'transform="rotate({math.degrees(a):.0f} {x:.1f} {y:.1f})" stroke="none" fill="#fff" fill-opacity=".92"/>'
              for x, y, a in leaves]
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="6 6 102 134" preserveAspectRatio="none" fill="none" stroke="#fff" '
            'stroke-linecap="round">' + "".join(parts) + "</svg>")


US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
    "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
    "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico",
    "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont",
    "virginia", "washington", "west virginia", "wisconsin", "wyoming", "district of columbia",
}
US_NAMES = {"usa", "u.s.a.", "u.s.", "us", "united states", "united states of america"}


def split_place(place: str):
    """Split 'street, city, county, state, country' style names for grouping.

    Returns (country, levels, address). `levels` is the list of headings between the country and
    the address: [region] for most countries, and [state, city] for the United States. Counties
    are never grouped on: with two or more parts before the state, the part nearest the state is
    assumed to be the county and is dropped entirely, and the next part in is the city; with just
    one part before the state, it's treated as the city/address directly (nothing to drop). US
    places that end in a state are filed under United States even when "USA" is missing.
    """
    parts = [x.strip() for x in place.split(",") if x.strip()]
    if not parts:
        return "Unknown", [], ""
    last = parts[-1].lower()
    if last in US_NAMES:
        country, rest = "United States", parts[:-1]
        state_known = bool(rest) and rest[-1].lower() in US_STATES
    elif last in US_STATES:
        country, rest, state_known = "United States", parts, True
    else:
        country, rest = parts[-1], parts[:-1]
        if not rest:
            return country, [], ""
        return country, [rest[-1]], ", ".join(rest[:-1])
    # United States
    if not rest:
        return country, [], ""
    if not state_known:                       # e.g. "Chicago, USA": no state given
        return country, [rest[-1]], ", ".join(rest[:-1])
    state, before = rest[-1], rest[:-1]
    if len(before) >= 2:                       # e.g. "Street, City, County, State" or "City, County,
        cb = before[:-1]                        # State" -- the part nearest the state is the county,
        city = cb[-1]                           # dropped entirely rather than grouped on
        return country, [state, city], ", ".join(cb[:-1])
    if len(before) == 1:
        return country, [state], before[0]
    return country, [state], ""


def short_place(place: str) -> str:
    """'City, State' for US places, 'City, Country' elsewhere (street addresses, counties, provinces dropped).
    A place that starts with a camp name (the whole word "Camp") keeps that name: 'Auschwitz Death Camp, Poland'."""
    parts = [x.strip() for x in place.split(",") if x.strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    last = parts[-1].lower()
    us = last in US_NAMES or last in US_STATES
    if us:
        parts = [x.strip() for x in drop_county(place).split(",") if x.strip()]
        if parts[-1].lower() in US_NAMES and len(parts) > 1 and parts[-2].lower() in US_STATES:
            parts = parts[:-1]                    # end on the state, not on "USA"
    end = parts[-1]                               # the state (US) or the country
    rest = parts[:-1]
    if not rest:
        return end
    if re.search(r"\bcamp\b", rest[0], re.I):
        return rest[0] + ", " + end
    whole = ("camp", "street", "road", "avenue", "apt", "building", "home", "church", "prison", "ghetto")
    inside = ("hospital", "spital", "cemetery", "friedhof", "kirche", "strasse", "straße", "cathedral", "synagogue")
    def bad(x):
        xl = x.lower()
        return (any(c.isdigit() for c in x) or any(w in xl for w in inside)
                or any(re.search(r"\b" + w + r"\b", xl) for w in whole))
    good = [x for x in rest if not bad(x)]
    if not good:
        return end
    return (good[-1] if us else good[0]) + ", " + end


def drop_county(place: str) -> str:
    """Place name without its county / district (used on person pages only; the Places page keeps it).
    'Street, Town, County, State, USA' -> 'Street, Town, State, USA'. Only US places are changed, and only
    when there is a town or street as well as the county."""
    parts = [x.strip() for x in place.split(",") if x.strip()]
    if not parts:
        return place
    last = parts[-1].lower()
    if last in US_NAMES:
        rest = parts[:-1]
        state_known = bool(rest) and rest[-1].lower() in US_STATES
    elif last in US_STATES:
        rest, state_known = parts, True
    else:
        return place                                  # outside the US the middle part may be a city or province: keep it
    if state_known and len(rest) - 1 >= 2:
        del parts[len(rest) - 2]                        # the part just before the state
        return ", ".join(parts)
    return place


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------
class Site:
    def __init__(self, tree: Tree, cfg, out: Path):
        self.t, self.cfg, self.out = tree, cfg, out
        self.header_name = ("header" + Path(cfg["header_image"]).suffix.lower()) if cfg["header_image"] else ""
        self.header_ratio = 0.1
        if self.header_name and Image is not None:
            try:
                with Image.open(cfg["header_image"]) as hi:
                    self.header_ratio = hi.height / hi.width
            except Exception:
                pass
        self.logo_name = "logo" + (Path(cfg["logo"]).suffix.lower() if cfg["logo"] else ".svg")
        self.visible = [p for p in tree.people.values() if not p.hidden]
        self.visible.sort(key=lambda p: (p.surname.lower() or "~", p.given.lower(),
                                         p.birth_year or 9999))
        self.today = datetime.date.today().strftime("%-d %B %Y") \
            if sys.platform != "win32" else datetime.date.today().isoformat()

        # name -> [Person,...] for linking full-name mentions in Life Story text (see notes_html)
        self.name_registry = defaultdict(list)
        for p in self.visible:
            words = p.name.split()
            if len(words) >= 2:                          # require a full name (given + surname)
                self.name_registry[" ".join(words).lower()].append(p)

    # -- small pieces -----------------------------------------------------
    @staticmethod
    def maplink(place):
        """Small 'Map' link that opens the place in Google Maps (new tab)."""
        url = "https://www.google.com/maps/search/?api=1&amp;query=" + quote(place)
        return (f'<a class="map" href="{url}" target="_blank" rel="noopener" '
                f'title="View {esc(place)} on Google Maps">Map</a>')

    def placelink(self, place, short=False):
        """Place name followed by its map link (the map always uses the full name)."""
        shown = drop_county(place) if short else place
        return f"{esc(shown)} {self.maplink(place)}" if place else ""

    def write(self, rel, content):
        path = self.out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def plink(self, p, root, cls=""):
        if p is None:
            return ""
        if p.hidden:
            return "<span class='living'>Living</span>"
        c = f' class="{cls}"' if cls else ""
        return f'<a{c} href="{root}people/{p.slug}.html">{esc(p.name)}</a>'

    def close_relatives(self, p):
        """Parents, siblings, spouses and children of p - used to disambiguate a shared name in Life Story text."""
        t = self.t
        dad, mom = t.parents(p)
        rel = {x for x in (dad, mom) if x}
        for u in t.unions(p):
            if u["spouse"]:
                rel.add(u["spouse"])
            rel.update(u["kids"])
        for par in (dad, mom):
            if par:
                for u in t.unions(par):
                    rel.update(k for k in u["kids"] if k is not p)
        return rel

    def notes_html(self, p):
        """Life Story text as paragraphs, with full-name mentions of other people linked to their pages.
        Only names that match exactly one visible person - or, when several people share a name, exactly
        one close relative of p - are linked; anything else stays plain text."""
        root = "../"
        rel = None  # computed lazily, only if a name is actually ambiguous
        chosen = {}
        for nm, people in self.name_registry.items():
            cands = [x for x in people if x is not p]
            if not cands:
                continue
            if len(cands) == 1:
                chosen[nm] = cands[0]
            else:
                if rel is None:
                    rel = self.close_relatives(p)
                close = [x for x in cands if x in rel]
                if len(close) == 1:
                    chosen[nm] = close[0]
                # else: still ambiguous - leave unlinked
        pattern = None
        if chosen:
            names_by_len = sorted(chosen, key=len, reverse=True)
            pattern = re.compile(r"\b(?:" + "|".join(re.escape(n) for n in names_by_len) + r")\b", re.IGNORECASE)

        tokens = []

        def mark(text):
            if not pattern:
                return text
            def repl(m):
                person = chosen.get(m.group(0).lower())
                if not person:
                    return m.group(0)
                tokens.append(f'<a href="{root}people/{person.slug}.html"><strong>{esc(m.group(0))}</strong></a>')
                return f"\uE000{len(tokens) - 1}\uE001"
            return pattern.sub(repl, text)

        html_out = "".join(paras(mark(n)) for n in p.notes)

        def restore(m):
            return tokens[int(m.group(1))]
        return re.sub(r"\uE000(\d+)\uE001", restore, html_out)

    @staticmethod
    def face_crop(p, img):
        """If p is face-tagged on img, (cx%, cy%, zoom scale) to center and zoom in on that face; else None."""
        if img is None:
            return None
        tag = next((t for t in img.tags if t[0] is p), None)
        if not tag:
            return None
        _, x, y, r = tag
        cx, cy = round(x * 100, 2), round(y * 100, 2)
        scale = max(1.0, min(6.0, 0.42 / max(2 * r, 0.02)))
        return cx, cy, round(scale, 2)

    def avatar(self, p, root, size="sm", crop=False):
        """crop: if p is face-tagged on their own portrait photo, zoom in on that face instead of showing it centered."""
        img = None if p.hidden else p.portrait
        if img:
            fc = self.face_crop(p, img) if crop else None
            if fc:
                cx, cy, scale = fc
                return (f'<span class="av {size} avwrap"><img loading="lazy" '
                        f'src="{root}media/thumb/{img.name}" alt="" '
                        f'style="object-position:{cx}% {cy}%;transform:scale({scale});'
                        f'transform-origin:{cx}% {cy}%"></span>')
            return (f'<img class="av {size}" loading="lazy" '
                    f'src="{root}media/thumb/{img.name}" alt="">')
        initials = "".join(w[0] for w in p.name.split()[:2]).upper() or "?"
        return f'<span class="av ph {size}" aria-hidden="true">{esc(initials)}</span>'

    def famcard(self, q, root, me=False, extra=""):
        """A person tile (portrait, name, years) used in the Family section."""
        sex = {"M": " m", "F": " f"}.get(q.sex, "")
        name = f"<strong>{esc(q.name)}</strong>" if me else self.plink(q, root)
        yrs = f'<span class="yrs">{esc(q.lifespan)}</span>' if q.lifespan else ""
        return (f'<div class="pc fc{sex}{" me" if me else ""}">{self.avatar(q, root, "xs", crop=True)}'
                f'<div>{name}{yrs}{extra}</div></div>')

    def shell(self, title, body, root, nav="", desc=""):
        c = self.cfg
        full = f"{title} · {c['site_title']}" if title else c["site_title"]

        def a(href, label, key=None):
            cur = ' aria-current="page"' if key and key == nav else ""
            return f'<a href="{root}{href}"{cur}>{label}</a>'

        menu = (f'<li>{a("index.html", "Home", "home")}</li>'
                f'<li>{a("people/index.html", "People", "people")}</li>'
                f'<li>{a("places.html", "Places", "places")}</li>'
                f'<li>{a("pedigree.html", "Chart", "pedigree")}</li>'
                f'<li>{a("gallery.html", "Photos", "gallery")}</li>'
                f'<li class="navsearch"><form action="{root}people/index.html" method="get" role="search">'
                f'<input name="q" type="search" placeholder="Search the tree…" '
                f'aria-label="Search people"><button aria-label="Search">Search</button></form></li>')

        def hl(item):
            url = item["url"]
            href = url if re.match(r"^(https?:|mailto:)", url) else root + url
            return f'<a href="{esc(href)}">{esc(item["label"])}</a>'
        toplinks = "".join(hl(i) for i in c["header_links"])

        contact = ""
        if c["contact_email"]:
            # The address is not written into the page as text: it is stored encoded and only
            # turned into a mailto: link when someone clicks (see assets/site.js).
            enc = base64.b64encode(c["contact_email"].strip().encode()).decode()
            contact = (f'<div class="fmain"><div class="wrap"><ul class="ql"><li>'
                       f'<a href="#contact" class="contact" data-m="{enc}">Contact Me</a></li></ul></div></div>')
        year = datetime.date.today().year
        hdr_style = ""
        if self.header_name:
            hdr_style = (' style="--hr:%.4f;background-image:linear-gradient(rgba(36,41,49,.5),rgba(36,41,49,.35)),url(\'%sassets/%s\')"'
                         % (self.header_ratio, root, self.header_name))
        hnote = ""
        if c.get("webmaster_message"):               # a line break in the message starts a new line in the header
            lines = [" ".join(x.split()) for x in str(c["webmaster_message"]).splitlines() if x.strip()]
            body_lines = [esc(x) for x in lines]
            if len(body_lines) > 1:                  # the last line is the signature, in a script font
                body_lines[-1] = f'<span class="sig">{body_lines[-1]}</span>'
            hnote = '<p class="hnote">' + "<br>".join(body_lines) + "</p>"
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(full)}</title>
<meta name="description" content="{esc(desc or c['tagline'])}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,500;0,600;1,500&family=Dancing+Script:wght@500&family=Jura:wght@400;500&family=Open+Sans:wght@400;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{root}assets/style.css">
</head><body data-newtab="{esc(c['new_tab_links'])}">
<header class="top{' has-img' if self.header_name else ''}"{hdr_style}><div class="hd wrap">
<a class="logo" href="{root}index.html" aria-label="Home"><img src="{root}assets/{self.logo_name}" alt=""></a>
<div class="titles"><a class="brand" href="{root}index.html">{esc(c['site_title'])}</a>
</div>
{hnote}
{f'<div class="toplinks">{toplinks}</div>' if toplinks else ""}</div>
<nav class="bar" aria-label="Main"><ul class="wrap">{menu}</ul></nav></header>
<main class="wrap">{body}</main>
<footer>{contact}
<div class="fbar"><div class="wrap">&copy; {year} {esc(c['copyright_text'] or c['site_title'])} &middot; Last updated {esc(self.today)}</div></div>
</footer>
<script src="{root}assets/site.js"></script>
</body></html>"""

    @staticmethod
    def photo_href(img, root):
        """Tagged photos open their own page (with names on the faces); others open full size."""
        if img.tags:
            return f"{root}photo/{Path(img.name).stem}.html"
        return f"{root}media/web/{img.name}"

    def figure(self, img, root, with_people=True, focus=None):
        """focus: when given and this person has a face tag on img, the thumbnail is zoomed to their face."""
        names = [q.name for q in img.people]
        plain = img.title or ", ".join(names)
        if img.title and names:
            plain = f"{img.title} — {', '.join(names)}"
        who = ""
        if with_people and img.people:
            who = "<br>".join(self.plink(q, root) for q in img.people[:6])
            if len(img.people) > 6:
                who += f"<br>+{len(img.people) - 6} more"
        search = " ".join([img.title] + names).lower()
        cap = f"<strong>{esc(img.title)}</strong><br>" if img.title else ""
        style = ""
        if focus is not None:
            tag = next((t for t in img.tags if t[0] is focus), None)
            if tag:
                _, x, y, r = tag
                cx, cy = round(x * 100, 2), round(y * 100, 2)
                scale = max(1.0, min(6.0, 0.42 / max(2 * r, 0.02)))     # zoom so the face fills ~42% of the frame
                style = f' style="object-position:{cx}% {cy}%;transform:scale({scale:.2f});transform-origin:{cx}% {cy}%"'
        return (f'<figure data-s="{esc(search)}"><a href="{self.photo_href(img, root)}" '
                f'target="_blank" rel="noopener" title="{esc(plain)}"><span class="fcwrap"><img loading="lazy" '
                f'src="{root}media/thumb/{img.name}" alt="{esc(plain)}"{style}></span></a>'
                f'<figcaption>{cap}{who}</figcaption></figure>')

    # -- person page ------------------------------------------------------
    def pedigree(self, p, root, gen, maxgen):
        sexcls = {"M": " m", "F": " f"}.get(p.sex, "")
        card = (f'<div class="pc{sexcls}">{self.plink(p, root)}'
                f'<span class="yrs">{esc(p.lifespan)}</span></div>')
        kids = ""
        if gen < maxgen:
            dad, mom = self.t.parents(p)
            sub = [self.pedigree(q, root, gen + 1, maxgen) for q in (dad, mom) if q]
            if sub:
                kids = f'<div class="pp">{"".join(sub)}</div>'
        return f'<div class="pn">{card}{kids}</div>'

    def descendants(self, p, root, depth, maxdepth):
        items = []
        for u in self.t.unions(p):
            for c in u["kids"]:
                sub = ""
                if depth < maxdepth and self.t.unions(c):
                    sub = self.descendants(c, root, depth + 1, maxdepth)
                head = f'{self.plink(c, root)} <span class="yrs">{esc(c.lifespan)}</span>'
                if sub:   # has children of their own: arrow collapses the next generation
                    items.append(f'<li><details class="dn" open><summary>{head}</summary>{sub}</details></li>')
                else:
                    items.append(f'<li class="leaf">{head}</li>')
        return f"<ul class='desc'>{''.join(items)}</ul>" if items else ""

    def family_tree(self, p, root, dad, mom):
        """Family chart: parents above, person with primary spouse beside, children below."""
        unions = self.t.unions(p)
        u = unions[0] if unions else None
        if not (dad or mom or u):
            return ""
        card = self.famcard

        # parents (above the person), joined by a line; the stem lands on the person's card
        top = ""
        if dad or mom:
            if dad and mom:
                pg = (f"<div class='pg'><div class='pl'>{card(dad, root)}</div><span class='pbond'></span>"
                      f"<div class='pr'>{card(mom, root)}</div></div>")
            else:
                pg = f"<div class='pg one'>{card(dad or mom, root)}</div>"
            top = f"<span class='ft-tag first'>Parents</span>{pg}<span class='pstem'></span>"
        line = "<span class='ml'></span>" if u else ""
        mine = (f"<div class='mrow'><span class='ms'></span>{card(p, root, me=True)}{line}</div>")
        left = f"<div class='cl'>{top}{mine}</div>"

        if not u:
            return f"<section><h2>Family</h2><div class='ft'><div class='cg solo'>{left}</div></div></section>"

        sp = (card(u["spouse"], root) if u["spouse"]
              else "<div class='pc fc ghost'><div>Unknown spouse</div></div>")
        kids = ""
        if u["kids"]:
            lis = "".join(f"<li>{card(c, root)}</li>" for c in u["kids"])
            kids = ("<div class='ck'><div class='sub'><span class='stem'></span>"
                    f"<span class='ft-tag'>Children</span><ul>{lis}</ul></div></div>")
        cb = "cb kids" if u["kids"] else "cb"
        return (f"<section><h2>Family</h2><div class='ft'><div class='cg'>{left}"
                f"<span class='{cb}'></span><div class='cr'>{sp}</div>{kids}</div></div></section>")

    def person_page(self, p):
        root = "../"
        t = self.t
        dad, mom = t.parents(p)

        facts = []
        for e in p.events:
            sp = e.get("spouse")
            spouse = f"<strong>{self.plink(sp, root)}</strong>" if sp else ""
            main = " · ".join(x for x in (esc(e["date"]), spouse, self.placelink(e["place"], short=True)) if x)
            desc = " ".join(rich_link(x) for x in (e["text"], e["note"]) if x)
            if main and desc:
                note = f" <span class='enote'>· {desc}</span>"
            elif desc:
                main, note = f"<span class='enote'>{desc}</span>", ""
            else:
                note = ""
            allowed = {img.key for img, _, _ in p.media}
            pics = [i for i in e["media"] if i.key in allowed and i.key in t.images]
            thumbs = ""
            if pics:
                shown = "".join(
                    f'<a href="{self.photo_href(i, root)}" target="_blank" rel="noopener" '
                    f'title="{esc(i.title or e["label"])}"><img loading="lazy" '
                    f'src="{root}media/thumb/{i.name}" alt="{esc(i.title or e["label"])}"></a>'
                    for i in pics[:8])
                more = f'<span class="more">+{len(pics) - 8}</span>' if len(pics) > 8 else ""
                thumbs = f'<div class="evthumbs">{shown}{more}</div>'
            facts.append(f"<tr><th>{esc(e['label'])}</th><td><div class='ev'><div class='evt'>"
                         f"{main}{note}</div>{thumbs}</div></td></tr>")
        events = f"<section><h2>Life events</h2><table class='facts'>{''.join(facts)}</table></section>" \
            if facts else ""

        family = self.family_tree(p, root, dad, mom)


        notes = ""
        if p.notes:
            notes = "<section><h2>Life Story</h2><div class='prose'>" + \
                    self.notes_html(p) + "</div></section>"
        gallery = ""
        if p.media:
            seen, figs = set(), []
            for img in sorted({m[0].key: m[0] for m in p.media}.values(), key=file_key):
                if img.key not in seen:
                    seen.add(img.key)
                    figs.append(self.figure(img, root, with_people=True))
            gallery = f"<section><h2>Photos</h2><div class='grid'>{''.join(figs)}</div></section>"
        hero = (f'<div class="hero">{self.avatar(p, root, "lg", crop=True)}<div>'
                f'<h1>{esc(p.name)}</h1><p class="lead">{esc(p.lifespan)}</p></div></div>')
        def fold(section):
            """Turn '<section><h2>X</h2>...</section>' into a collapsible block (open by default)."""
            return re.sub(r"^<section><h2>(.*?)</h2>(.*)</section>$",
                          r'<details class="sec" open><summary><h2>\1</h2></summary>\2</details>',
                          section, flags=re.S) if section else ""

        body = hero + "".join(fold(x) for x in (notes, events, family, gallery))
        self.write(f"people/{p.slug}.html",
                   self.shell(p.name, body, root, "people", f"{p.name} {p.lifespan}".strip()))

    # -- indexes ----------------------------------------------------------
    def people_index(self):
        """People page: everyone, A to Z by surname, with a letter strip on the right to jump."""
        import unicodedata
        root = "../"

        def letter(p):
            ch = unicodedata.normalize("NFKD", p.surname[:1] or "?")[:1].upper()
            return ch if "A" <= ch <= "Z" else "#"

        by_letter = defaultdict(list)
        for p in self.visible:                      # self.visible is already sorted by surname, then given name
            by_letter[letter(p)].append(p)
        order = [c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c in by_letter] + (["#"] if "#" in by_letter else [])
        rows = []
        for ch in order:
            rows.append(f'<li class="la" id="L-{"other" if ch == "#" else ch}" aria-hidden="true"></li>')  # jump target
            for p in by_letter[ch]:
                place = (p.event("BIRT") or {}).get("place", "")
                be = p.event("BIRT") or p.event("CHR") or p.event("BAPM")
                de = p.event("DEAT") or p.event("BURI")
                bd, dd = (be or {}).get("date", ""), (de or {}).get("date", "")
                bp, dp = short_place((be or {}).get("place", "")), short_place((de or {}).get("place", ""))
                bits = []
                for tag, dt, pl in (("b.", bd, bp), ("d.", dd, dp)):
                    if dt or pl:
                        bits.append(f"{tag} " + ", ".join(x for x in (dt, pl) if x))
                dates = " · ".join(bits)
                search = f"{p.name} {place} {(de or {}).get('place', '')} {bp} {dp} {p.lifespan} {bd} {dd}".lower()
                rows.append(f'<li data-s="{esc(search)}">{self.plink(p, root)}'
                            f'<span class="yrs">{esc(dates)}</span></li>')
        out = [f'<ul class="people plain">{"".join(rows)}</ul>']
        strip = "".join(
            (f'<a href="#L-{c}" data-l="{c}">{c}</a>' if c in order else f'<span class="none">{c}</span>')
            for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        if "#" in order:
            strip += '<a href="#L-other" data-l="other">#</a>'
        body = (f"<div class='peoplepage'><div class='plist'><h1>People</h1><p class='lead'>{len(self.visible):,} people, "
                "A to Z by surname. Use the letters on the right to jump, or type to filter by name, place or year.</p>"
                '<input id="q" type="search" placeholder="Search people…" autocomplete="off">'
                + "".join(out) + '<p id="none" hidden>No matches.</p></div>'
                f'<nav class="az" aria-label="Jump to letter">{strip}</nav></div>')
        self.write("people/index.html", self.shell("People", body, root, "people"))

    def places(self):
        by = defaultdict(list)
        for p in self.visible:
            seen = set()
            for e in p.events:
                if e["place"] and (e["place"], e["label"], e["year"]) not in seen:
                    seen.add((e["place"], e["label"], e["year"]))
                    by[e["place"]].append((p, e))

        def node():
            return {"items": [], "kids": defaultdict(node)}
        root = defaultdict(node)                 # country -> nested {"items", "kids"}
        for place in by:
            country, levels, address = split_place(place)
            cur = root[country]
            for lvl in levels:
                cur = cur["kids"][lvl]
            cur["items"].append((address, place, levels[-1] if levels else country))

        def entry(label, place):
            """One row per place (not collapsible - its one line of people is always shown).
            A place with no street address is labelled with
            the name of the country / region it belongs to."""
            ykey = lambda e: (e["year"] is None, e["year"] or 0)
            grouped = {}                                  # one row per person, all their events here
            for p, e in by[place]:
                grouped.setdefault(p.id, (p, []))[1].append(e)
            people = sorted(grouped.values(), key=lambda pe: (pe[0].surname.lower(), pe[0].given.lower()))
            def yrs(e):
                if e["year_end"]:
                    return f' {e["year"]}-{e["year_end"]}'
                return f' {e["year"]}' if e["year"] else ""
            lis = "".join(
                f'<li>{self.plink(p, "")} <span class="yrs">'
                + " · ".join(esc(e["label"].lower()) + yrs(e)
                             for e in sorted(evs, key=ykey))
                + '</span></li>' for p, evs in people)
            search = " ".join([place] + [p.name for p, _ in people]).lower()
            return (f'<div class="pl" data-s="{esc(search)}">{esc(label)} '
                    f'<span class="yrs">{len(people)}</span> {self.maplink(place)}'
                    f'<ul class="plain">{lis}</ul></div>')

        def fold(n):
            """'Broward, Florida' sits next to a 'Broward' county: file it inside that county."""
            kids = {k.lower(): k for k in n["kids"]}
            keep = []
            for addr, pl, owner in n["items"]:
                k = kids.get(addr.lower()) if addr else None
                if k:
                    n["kids"][k]["items"].append(("", pl, k))
                else:
                    keep.append((addr, pl, owner))
            n["items"] = keep
            for kid in n["kids"].values():
                fold(kid)
        for c in root.values():
            fold(c)

        def count(n):
            return len(n["items"]) + sum(count(k) for k in n["kids"].values())

        def render(name, n, cls):
            rows = "".join(entry(addr or owner, pl)
                           for addr, pl, owner in sorted(n["items"], key=lambda x: (x[0] != "", x[0].lower())))
            kids = "".join(render(k, n["kids"][k], "reg") for k in sorted(n["kids"], key=str.lower))
            return (f'<details class="group {cls}"><summary>{esc(name)} '
                    f'<span class="yrs">{count(n)}</span></summary>{rows}{kids}</details>')

        blocks = [render(c, root[c], "ctry") for c in sorted(root, key=str.lower)]
        body = ("<div class='prow'><h1>Places</h1><p class='lead'>Where my family was born, lived, married, and buried.</p></div>"
                '<input id="q" type="search" placeholder="Filter by Place or Person…" autocomplete="off">'
                '<p class="expand"><button type="button" id="pexpand">Expand all</button> '
                '<button type="button" id="psort">Sort by most people</button></p>'
                + "".join(blocks) + "<p id='none' hidden>No matches.</p>")
        self.write("places.html", self.shell("Places", body, "", "places"))

    def photo_pages(self):
        """One page per tagged photo: the picture with a marker and name on each tagged face."""
        for img in self.t.images.values():
            if not img.tags:
                continue
            root = "../"
            marks, names = [], []
            for i, (q, x, y, r) in enumerate(img.tags):
                link = self.plink(q, root)
                marks.append(f'<span class="tg" tabindex="0" style="left:{x * 100:.2f}%;top:{y * 100:.2f}%;width:{r * 200:.2f}%">'
                             f'<span class="lab">{link}</span></span>')
                names.append(f'<li><a href="{root}people/{q.slug}.html" data-t="{i}">{esc(q.name)}</a></li>')
            tagged = {id(t[0]) for t in img.tags}
            others = [q for q in img.people if id(q) not in tagged and not q.hidden]
            more = ""
            if others:
                more = ("<p class='dim'>Also linked to this photo: "
                        + ", ".join(self.plink(q, root) for q in others) + "</p>")
            title = img.title or "Photograph"
            alt = esc(title)
            body = (f"<p><a href='{root}gallery.html'>← All photos</a></p><h1>{esc(title)}</h1>"
                    f"<p class='dim thint'>Hover over a face to see who it is.</p><div class='tpbox'><div class='tphoto'><img src='{root}media/web/{img.name}' alt='{alt}'>"
                    f"{''.join(marks)}</div></div>"
                    f"<p class='tctl'><button id='tnames' type='button' class='tbtn'>Show all names</button> "
                    f"<a href='{root}media/web/{img.name}'>Open full-size image</a></p>"
                    f"<h3>In this photo</h3><ul class='plain tlist'>{''.join(names)}</ul>{more}")
            self.write(f"photo/{Path(img.name).stem}.html", self.shell(title, body, root, "gallery"))

    def pedigree_page(self):
        """Whole-tree chart: ancestors to the right, descendants to the left, an arrow between each person."""
        idx = {p.id: i for i, p in enumerate(self.visible)}
        recs = []
        for p in self.visible:
            dad, mom = self.t.parents(p)
            img = p.portrait
            sibs, seen = [], {p.id}
            for par in (dad, mom):
                for u in (self.t.unions(par) if par else []):
                    for c in u["kids"]:
                        if c.id in idx and c.id not in seen:
                            seen.add(c.id)
                            sibs.append(c)
            sibs.sort(key=lambda c: c.birth_sort)
            sb = [[idx[c.id], 0 if self.t.parents(c) == (dad, mom) else 1] for c in sibs]
            sps = []
            for u in self.t.unions(p):
                q = u["spouse"]
                if q is not None and q.id in idx:
                    m = u["marr"]
                    sps.append([idx[q.id], (m["date"] if m else "") or ""])
            kids = []
            for u in self.t.unions(p):
                for c in u["kids"]:
                    if c.id in idx and idx[c.id] not in kids:
                        kids.append(idx[c.id])
            rec = {"n": p.name, "y": p.lifespan, "s": p.slug, "t": img.name if img else "",
                   "x": p.sex, "f": idx.get(dad.id, -1) if dad else -1,
                   "m": idx.get(mom.id, -1) if mom else -1, "k": kids, "sb": sb, "sp": sps}
            fc = self.face_crop(p, img)
            if fc:
                rec["c"] = list(fc)
            recs.append(rec)
        if not recs:
            return
        memo = {}

        def ancestors(i, seen=()):
            if i in memo:
                return memo[i]
            if i in seen:
                return 0
            n = 0
            for k in (recs[i]["f"], recs[i]["m"]):
                if k >= 0:
                    n += 1 + ancestors(k, seen + (i,))
            memo[i] = n
            return n

        start = idx.get(self.cfg["home_person"], -1)
        if start < 0:
            start = max(range(len(recs)), key=lambda i: ancestors(i))
        blob = json.dumps({"p": recs, "start": start}, ensure_ascii=False).replace("</", "<\\/")
        opts = "".join(f'<option value="{esc(r["n"] + (" (" + r["y"] + ")" if r["y"] else ""))}">'
                       for r in recs)
        gens = "".join(f'<option value="{n}"{" selected" if n == 4 else ""}>{n}</option>' for n in range(2, 9))
        body = f"""<h1>Family chart</h1>
<p class="lead">Ancestors and descendants of <strong id="pdname"></strong>. Descendants are on the left and ancestors on the right. Click the arrow between two people to show or hide that person&#39;s children or parents, the small arrow above a card to show that person&#39;s spouses, and the one below to show siblings.</p>
<div class="pdbar">
<label>Start with <input id="pdq" type="search" list="pdl" autocomplete="off" placeholder="Type a name…"></label>
<label>Show <select id="pdw"><option value="both">Ancestors and descendants</option><option value="anc">Ancestors only</option><option value="desc">Descendants only</option></select></label>
<label>Open <select id="pdg">{gens}</select> generations</label>
<button type="button" class="tbtn" id="pdx">Expand all</button><button type="button" class="tbtn" id="pdc">Collapse all</button>
</div>
<datalist id="pdl">{opts}</datalist>
<div id="pd" class="pdwrap" aria-live="polite"></div>
<p class="dim" id="pdnote" hidden></p>
<script id="pddata" type="application/json">{blob}</script>"""
        self.write("pedigree.html", self.shell("Ancestors and descendants chart", body, "", "pedigree"))

    def gallery(self):
        imgs = sorted(self.t.images.values(), key=file_key)
        figs = "".join(self.figure(i, "") for i in imgs)
        body = (f"<h1>Photos</h1><p class='lead'>{len(imgs):,} photographs. "
                "Filter by caption or person; photos open full size in a new tab.</p>"
                '<input id="q" type="search" placeholder="Search photos…" autocomplete="off">'
                f"<div class='grid'>{figs}</div><p id='none' hidden>No matches.</p>")
        self.write("gallery.html", self.shell("Photos", body, "", "gallery"))

    def grandparents(self):
        """'The Grandparents' band on the home page: four photo cards, left to right."""
        want = list(self.cfg.get("grandparents") or [])[:4]
        if not any(str(w).strip() for w in want):
            return ""
        want += [""] * (4 - len(want))
        norm = lambda x: " ".join(re.sub(r"[.,]", " ", str(x).lower()).split())
        byname = {}
        for p in self.visible:
            byname.setdefault(norm(p.name), p)
        cards = []
        for key in want:
            label = ""
            if isinstance(key, dict):               # {"person": "Annie Laurie Linn Ford", "label": "Linn Ford"}
                label = str(key.get("label", "")).strip()
                key = key.get("person", "")
            key = str(key).strip()
            p = self.t.people.get(key) or byname.get(norm(key))
            if p is None and key:                   # forgiving match: same first and last name, any middle names
                k = norm(key).split()
                close = [q for n, q in byname.items() if len(k) > 1 and n.split()[:1] == k[:1] and n.split()[-1:] == k[-1:]]
                if len(close) == 1:
                    p = close[0]
            if key and (p is None or p.hidden):
                why = "is hidden because the tree marks them as living" if p is not None else "was not found in the tree"
                print(f"  Grandparent '{key}' {why}; leaving that spot blank.")
            if p is None or p.hidden:
                cards.append("<article class='gp blank' aria-hidden='true'><h3>&nbsp;</h3>"
                             "<div class='gpimg'></div></article>")
                continue
            href = f"people/{p.slug}.html"
            img = p.portrait
            if img:
                pic = (f"<a class='gpimg' href='{href}' tabindex='-1' aria-hidden='true'>"
                       f"<img loading='lazy' src='media/web/{img.name}' alt=''></a>")
            else:
                ini = "".join(w[0] for w in p.name.split()[:2]).upper() or "?"
                pic = f"<a class='gpimg ph' href='{href}' tabindex='-1' aria-hidden='true'><span>{esc(ini)}</span></a>"
            cards.append(f"<article class='gp'><h3><a href='{href}'>{esc(label or p.name)}</a></h3>{pic}</article>")
        return f"<section class='gpband'><h2>The Grandparents</h2><div class='gprow'>{''.join(cards)}</div></section>"

    def home(self):
        c, t = self.cfg, self.t
        picks = sorted(t.images.values(), key=file_key)[:12]
        strip = "".join(self.figure(i, "", with_people=True) for i in picks)
        strip = f"<section><h2>From the photo collection</h2><div class='grid'>{strip}</div>" \
                f"<p><a href='gallery.html'>See all photographs →</a></p></section>" if picks else ""
        body = f"""{self.grandparents()}{strip}"""
        self.write("index.html", self.shell("", body, "", "home"))

    def extras(self):
        self.write(".nojekyll", "")
        if self.cfg["domain"]:
            self.write("CNAME", self.cfg["domain"].strip() + "\n")
        self.write("404.html", self.shell(
            "Not found", "<h1>Page not found</h1><p>That page doesn't exist. "
            "<a href='/index.html'>Return home</a> or <a href='/people/index.html'>browse people</a>.</p>",
            "/", ""))
        assets = self.out / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        here = Path(__file__).parent / "assets"
        for name in ("style.css", "site.js"):
            shutil.copy2(here / name, assets / name)
        if self.header_name:
            shutil.copy2(self.cfg["header_image"], assets / self.header_name)
        if self.cfg["logo"]:
            shutil.copy2(self.cfg["logo"], assets / self.logo_name)
        else:
            (assets / self.logo_name).write_text(tree_svg(), encoding="utf-8")
        extra_src = self.cfg["_base"] / "extra"
        if extra_src.is_dir():
            shutil.copytree(extra_src, self.out / "extra", dirs_exist_ok=True)

    def build(self):
        if self.out.exists():
            for child in self.out.iterdir():
                if child.name == "media":
                    continue  # keep processed images so rebuilds are fast
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        for p in self.visible:
            self.person_page(p)
        self.people_index()
        self.places()
        self.gallery()
        self.photo_pages()
        self.pedigree_page()
        self.home()
        self.extras()


# --------------------------------------------------------------------------
def load_config(args):
    cfg = dict(DEFAULTS)
    base = Path.cwd()
    cfg_path = Path(args.config)
    if cfg_path.exists():
        cfg.update(json.loads(cfg_path.read_text(encoding="utf-8")))
        base = cfg_path.resolve().parent
    for key, arg in (("gedcom", args.gedcom), ("media_dir", args.media), ("output_dir", args.out)):
        if arg:
            cfg[key] = str(Path(arg).expanduser().resolve())
    for key in ("gedcom", "media_dir"):            # each may be one path or a list of paths
        vals = cfg[key] if isinstance(cfg[key], list) else [cfg[key]]
        cfg[key] = [Path(v).expanduser() if Path(v).expanduser().is_absolute()
                    else base / Path(v).expanduser() for v in vals if str(v).strip()]
    p = Path(cfg["output_dir"]).expanduser()
    cfg["output_dir"] = p if p.is_absolute() else (base / p)
    tp = Path(cfg["tags_file"]).expanduser()
    cfg["tags_file"] = tp if tp.is_absolute() else (base / tp)
    cfg["_base"] = base
    if cfg["header_image"]:
        hp = Path(cfg["header_image"]).expanduser()
        cfg["header_image"] = hp if hp.is_absolute() else (base / hp)
    if cfg["logo"]:
        lp = Path(cfg["logo"]).expanduser()
        cfg["logo"] = lp if lp.is_absolute() else (base / lp)
    return cfg


def main():
    ap = argparse.ArgumentParser(description="Build a static family-tree website from a GEDCOM file.")
    ap.add_argument("--config", default="site.json")
    ap.add_argument("--gedcom")
    ap.add_argument("--media", help="folder containing your images")
    ap.add_argument("--out", help="output folder (default docs)")
    args = ap.parse_args()
    cfg = load_config(args)

    for g in cfg["gedcom"]:
        if not g.exists():
            sys.exit(f"GEDCOM file not found: {g}\n"
                     "Export your tree from Family Tree Maker as GEDCOM and point site.json at it.")
    if Image is None:
        print("Note: Pillow is not installed, so images will be copied at full size.\n"
              "      Run `pip install pillow` for resized, repo-friendly images.")

    print(f"Settings: {Path(args.config).resolve()}")
    report = []
    index = build_media_index(cfg["media_dir"])
    tree = Tree(parse_gedcom(cfg["gedcom"]), cfg, index, report)
    out = cfg["output_dir"]
    out.mkdir(parents=True, exist_ok=True)
    process_images(tree, out, cfg, report)
    tagged = apply_tags(tree, cfg["tags_file"], report)
    write_tagger(tree, cfg, out, cfg["_base"])
    site = Site(tree, cfg, out)
    site.build()

    # drop processed images that are no longer used
    keep = {i.name for i in tree.images.values()}
    for sub in ("web", "thumb"):
        for f in (out / "media" / sub).glob("*"):
            if f.name not in keep:
                f.unlink()

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 1e6
    print(f"Built {len(site.visible)} people, {len(tree.images)} images -> {out} ({size:,.0f} MB)")
    if tagged:
        print(f"{tagged} face tags applied (from {cfg['tags_file'].name}).")
    print("To tag faces in group photos, open tagger.html in your browser.")
    if report:
        uniq = sorted(set(report))
        Path("build-report.txt").write_text("\n".join(uniq), encoding="utf-8")
        print(f"{len(uniq)} image notes written to build-report.txt (first 5):")
        for line in uniq[:5]:
            print("  -", line)


if __name__ == "__main__":
    main()
