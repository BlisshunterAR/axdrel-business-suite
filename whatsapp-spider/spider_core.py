"""WhatsApp Appointment Spider - core logic.

Reads WhatsApp "Export chat" files (.txt or .zip, Android and iPhone formats),
keeps the messages inside a date range and builds a per-chat report:
phone number, saved name, name the client gave, what was ordered, address,
and a short summary of what was asked and what was planned.

Standard library only.
"""

import csv
import datetime as dt
import html
import io
import json
import os
import re
import urllib.request
import zipfile

INVISIBLE = dict.fromkeys(map(ord, "‎‏‪‫‬‭‮﻿"), None)

# Date, time and optional AM/PM shared by both formats.
_DT = (r"(\d{1,4})[./-](\d{1,2})[./-](\d{2,4}),?\s+"
       r"(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?\s*([AaPp]\.?\s?[Mm]\.?)?")
ANDROID_RE = re.compile(r"^" + _DT + r"\s*[-–]\s+(.*)$")
IOS_RE = re.compile(r"^\[" + _DT + r"\]\s*(.*)$")

PHONE_RE = re.compile(r"^\+?[\d\s\-().]{7,}$")

MEDIA_MARKERS = ("<media omitted>", "<media weggelaten>", "image omitted", "afbeelding weggelaten",
                 "video omitted", "audio omitted", "sticker omitted", "document omitted",
                 "<bijlage:", "<attached:", "gif omitted", "this message was deleted",
                 "dit bericht is verwijderd", "you deleted this message")

TITLE_PREFIXES = [
    r"whatsapp[\s_-]*chat[\s_-]*(with|met|mit|con|com|avec|z|med)\s+",
    r"whatsapp[\s_-]*chat[\s_-]*[-–]\s*",
    r"whatsapp[\s_-]*chat[\s_-]+",
    r"chat[\s_-]*(with|met|mit|con|com|avec)\s+",
]


# ---------------------------------------------------------------- reading

class Message:
    __slots__ = ("when", "sender", "text")

    def __init__(self, when, sender, text):
        self.when = when
        self.sender = sender
        self.text = text


def clean_line(line):
    return line.translate(INVISIBLE).replace(" ", " ").replace(" ", " ").rstrip("\r\n")


def chat_title_from_filename(path):
    name = os.path.splitext(os.path.basename(path))[0]
    name = re.sub(r"\s*\(\d+\)$", "", name)  # "WhatsApp Chat with X (1)"
    for pat in TITLE_PREFIXES:
        new = re.sub("^" + pat, "", name, flags=re.I)
        if new != name:
            name = new
            break
    return name.replace("_", " ").strip() or os.path.basename(path)


def read_export_text(path):
    """Return the chat text of a .txt export or the .txt inside a .zip export."""
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            txts = [n for n in zf.namelist() if n.lower().endswith(".txt")]
            if not txts:
                return None
            # iPhone uses _chat.txt, Android uses "WhatsApp Chat with X.txt"
            txts.sort(key=lambda n: (os.path.basename(n) != "_chat.txt", -zf.getinfo(n).file_size))
            raw = zf.read(txts[0])
    else:
        with open(path, "rb") as f:
            raw = f.read()
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _match(line):
    m = IOS_RE.match(line) or ANDROID_RE.match(line)
    return m


def detect_date_order(lines, default="DMY"):
    """Guess whether the export writes day-month-year, month-day-year or year-month-day."""
    a_big = b_big = False
    for line in lines:
        m = _match(line)
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if len(m.group(1)) == 4:
            return "YMD"
        if a > 12:
            a_big = True
        if b > 12:
            b_big = True
    if a_big and not b_big:
        return "DMY"
    if b_big and not a_big:
        return "MDY"
    return default


def _to_datetime(m, order):
    a, b, c = m.group(1), m.group(2), m.group(3)
    if order == "YMD":
        y, mo, d = int(a), int(b), int(c)
    elif order == "MDY":
        mo, d, y = int(a), int(b), int(c)
    else:
        d, mo, y = int(a), int(b), int(c)
    if y < 100:
        y += 2000
    hh, mm, ss = int(m.group(4)), int(m.group(5)), int(m.group(6) or 0)
    ampm = (m.group(7) or "").lower().replace(".", "").replace(" ", "")
    if ampm == "pm" and hh < 12:
        hh += 12
    elif ampm == "am" and hh == 12:
        hh = 0
    return dt.datetime(y, mo, d, hh, mm, ss)


def parse_chat(text, default_order="DMY"):
    lines = [clean_line(l) for l in text.splitlines()]
    order = detect_date_order(lines, default_order)
    messages = []
    for line in lines:
        m = _match(line)
        if m:
            try:
                when = _to_datetime(m, order)
            except ValueError:
                when = None
            if when is not None:
                rest = m.group(8)
                if ": " in rest:
                    sender, body = rest.split(": ", 1)
                else:
                    sender, body = None, rest  # system message
                messages.append(Message(when, sender and sender.strip(), body.strip()))
                continue
        if messages and line.strip():
            messages[-1].text += "\n" + line.strip()
    for m in messages:
        m.text = _strip_media(m.text) if m.text else m.text
    return messages


def load_exports(folder, default_order="DMY"):
    """Read every export in the folder. If the same chat was exported twice, keep the newest file."""
    chats = {}
    for root, _dirs, files in os.walk(folder):
        for fn in files:
            if not fn.lower().endswith((".txt", ".zip")):
                continue
            path = os.path.join(root, fn)
            try:
                text = read_export_text(path)
            except (zipfile.BadZipFile, OSError):
                continue
            if not text:
                continue
            msgs = parse_chat(text, default_order)
            if not msgs:
                continue
            title = chat_title_from_filename(path)
            if title.lower() in ("chat", "_chat", "") and path.lower().endswith(".txt"):
                title = os.path.basename(root)
            mtime = os.path.getmtime(path)
            key = title.lower()
            if key not in chats or chats[key]["mtime"] < mtime:
                chats[key] = {"title": title, "path": path, "mtime": mtime, "messages": msgs}
    return list(chats.values())


# ---------------------------------------------------------------- contacts

def normalize_number(s):
    if not s:
        return ""
    s = s.strip()
    plus = s.startswith("+") or s.startswith("00")
    digits = re.sub(r"\D", "", s)
    if s.startswith("00"):
        digits = digits[2:]
    return ("+" if plus else "") + digits


def looks_like_number(s):
    return bool(s) and bool(PHONE_RE.match(s.strip())) and len(re.sub(r"\D", "", s)) >= 6


def load_contacts(folder):
    """Optional: contacts.vcf or contacts.csv files in the folder map names <-> numbers."""
    name_to_num, num_to_name = {}, {}

    def add(name, num):
        name, num = (name or "").strip(), normalize_number(num)
        if not name or len(re.sub(r"\D", "", num)) < 6:
            return
        name_to_num.setdefault(name.lower(), num)
        num_to_name.setdefault(re.sub(r"\D", "", num)[-7:], name)

    if not folder or not os.path.isdir(folder):
        return name_to_num, num_to_name
    for root, _d, files in os.walk(folder):
        for fn in files:
            path = os.path.join(root, fn)
            low = fn.lower()
            try:
                if low.endswith(".vcf"):
                    with open(path, encoding="utf-8", errors="replace") as f:
                        name, nums = None, []
                        for line in f:
                            line = line.strip()
                            up = line.upper()
                            if up.startswith("BEGIN:VCARD"):
                                name, nums = None, []
                            elif up.startswith("FN"):
                                name = line.split(":", 1)[-1]
                            elif up.startswith("TEL"):
                                nums.append(line.split(":", 1)[-1])
                            elif up.startswith("END:VCARD"):
                                for n in nums:
                                    add(name, n)
                elif low.endswith(".csv") and "contact" in low:
                    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            name = ""
                            for k in ("Name", "name", "Naam", "naam", "Full Name", "Display Name"):
                                if row.get(k):
                                    name = row[k]
                                    break
                            if not name:
                                name = " ".join(filter(None, [row.get("First Name") or row.get("Given Name"),
                                                              row.get("Last Name") or row.get("Family Name")]))
                            for k, v in row.items():
                                if k and v and re.search(r"phone|telefoon|mobile|mobiel|nummer|number", k, re.I) \
                                        and not re.search(r"type|label", k, re.I):
                                    for part in v.split(":::"):
                                        add(name, part)
            except OSError:
                continue
    return name_to_num, num_to_name


# ---------------------------------------------------------------- who is who

def detect_my_names(chats):
    """The sender that shows up in the most chats is the phone owner."""
    counts = {}
    for c in chats:
        for s in {m.sender for m in c["messages"] if m.sender}:
            counts[s] = counts.get(s, 0) + 1
    if len(chats) < 2 or not counts:
        return set()
    top = max(counts.values())
    if top < 2:
        return set()
    return {s for s, n in counts.items() if n == top}


def identify_client(chat, my_names, name_to_num, num_to_name):
    title = chat["title"]
    senders = [m.sender for m in chat["messages"] if m.sender]
    distinct = list(dict.fromkeys(senders))
    others = [s for s in distinct if s not in my_names]
    is_group = len(others) > 1 if my_names else len(distinct) > 2
    if title in distinct:
        client_senders = {title}
    elif looks_like_number(title):
        t = re.sub(r"\D", "", title)[-7:]
        client_senders = {s for s in distinct if re.sub(r"\D", "", s)[-7:] == t} or set(others)
    else:
        client_senders = set(others)
    if looks_like_number(title):
        number = title.strip()
        saved = num_to_name.get(re.sub(r"\D", "", number)[-7:], "")
    else:
        saved = title
        number = name_to_num.get(title.lower(), "")
        if not number:
            # the client may appear as a number in a group / or title differs
            for s in others:
                if looks_like_number(s):
                    number = normalize_number(s)
                    break
    if not client_senders:
        client_senders = {title}
    return {"number": number, "saved_name": saved, "is_group": is_group,
            "client_senders": client_senders}


# ---------------------------------------------------------------- basic extraction (no AI)

NAME_PATTERNS = [
    r"\b(?:my name is|mijn naam is|ik heet|ik ben|this is|it'?s|met|naam\s*:|name\s*:|mi nen)\s+"
    r"([A-ZÀ-ſ][a-zÀ-ſ'\-]+(?:\s+(?:van|de|der|den|da|dos|el)?\s*[A-ZÀ-ſ][a-zÀ-ſ'\-]+){0,2})",
]
ADDRESS_WORDS = (r"straat|str\.|weg\b|laan\b|plein\b|pad\b|gracht|kade\b|dreef|singel|hof\b|steeg|"
                 r"street|st\.|road\b|rd\.|avenue|ave\b|lane\b|drive\b|blvd|boulevard|"
                 r"rua\b|avenida|calle")
ADDRESS_RE = re.compile(
    r"(\b[\w\u00C0-\u017F.'\-]*(?i:" + ADDRESS_WORDS + r")[\w\u00C0-\u017F.'\-]*\s*(?:no\.?|nr\.?|#)?\s*\d+[a-zA-Z]?"
    r"(?:,?\s+[A-Z][\w\-]+){0,2})"
    r"|(\b\d+[a-zA-Z]?\s+(?:[A-Z][\w\u00C0-\u017F'\-]*\s+){1,3}(?i:" + ADDRESS_WORDS + r")(?:,?\s+[A-Z][\w\-]+){0,2})")
ADDRESS_KEY_RE = re.compile(r"\b(?:adres|address|addres|adress|woonadres|bezorgadres|locatie|location)\b\s*(?:is|:|=)?\s*(.+)", re.I)
ORDER_RE = re.compile(r"\b(bestel\w*|order\w*|wil graag|ik wil|i want|i would like|i'?d like|i need|mag ik|kan ik|"
                      r"heb(?:ben)? nodig|kopen|buy|price|prijs|hoeveel|how much|offerte|quote|levering|delivery|"
                      r"\d+\s*(?:x|stuks|pcs|pieces|st\.)\b)", re.I)
TIME_RE = re.compile(r"\b(vandaag|morgen|overmorgen|today|tomorrow|tonight|vanavond|vanmiddag|vanochtend|"
                     r"maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag|"
                     r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
                     r"\d{1,2}[:.]\d{2}\s*(?:uur|u|am|pm)?|\d{1,2}\s*(?:uur|am|pm)\b|om \d{1,2}|at \d{1,2}|"
                     r"\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|afspraak|appointment|langs|komen|ophalen|pick ?up|bezorg\w*|deliver\w*)",
                     re.I)


def _short(s, n=160):
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _strip_media(text):
    keep = [l for l in text.split("\n") if not any(k in l.lower() for k in MEDIA_MARKERS)]
    return "\n".join(keep).strip()


def _useful(m):
    return bool(_strip_media(m.text))


def basic_extract(msgs, client_senders):
    client_msgs = [m for m in msgs if m.sender in client_senders and _useful(m)]
    my_msgs = [m for m in msgs if m.sender and m.sender not in client_senders and _useful(m)]

    name = ""
    for m in client_msgs:
        for pat in NAME_PATTERNS:
            mm = re.search(pat, m.text)
            if mm:
                name = mm.group(1).strip()
                break
        if name:
            break

    address = ""
    for m in client_msgs + my_msgs:
        mm = ADDRESS_KEY_RE.search(m.text)
        if mm and len(mm.group(1).strip()) > 3:
            address = _short(mm.group(1), 120)
            break
    if not address:
        for m in client_msgs:
            mm = ADDRESS_RE.search(m.text)
            if mm:
                address = _short(mm.group(1) or mm.group(2), 120)
                break

    orders = []
    for m in client_msgs:
        if ORDER_RE.search(m.text):
            orders.append(_short(m.text, 140))
        if len(orders) >= 3:
            break

    order_msgs = [m for m in client_msgs if ORDER_RE.search(m.text)]
    asked = _short((order_msgs or client_msgs)[0].text, 160) if client_msgs else ""
    planned = []
    for m in (my_msgs if any(TIME_RE.search(x.text) for x in my_msgs) else msgs):
        if _useful(m) and TIME_RE.search(m.text):
            planned.append(f"{m.when:%H:%M} {_short(m.text, 120)}")
    planned = planned[-3:]
    summary_parts = []
    if asked:
        summary_parts.append("Asked: " + asked)
    if planned:
        summary_parts.append("Planned: " + " | ".join(planned))
    if my_msgs and not (planned and planned[-1].endswith(_short(my_msgs[-1].text, 120))):
        summary_parts.append("Last reply from you: " + _short(my_msgs[-1].text, 120))
    return {
        "name_given": name,
        "order": " | ".join(orders),
        "address": address,
        "summary": "\n".join(summary_parts) or "No text messages in this period.",
        "method": "basic",
    }


# ---------------------------------------------------------------- AI extraction (optional)

AI_PROMPT = """You read one WhatsApp conversation between a business owner ("ME") and a client ("CLIENT").
Messages can be in Dutch, English, Sranan Tongo, Portuguese or a mix.
Return ONLY a JSON object with these keys, each a short string in English ("" if not mentioned):
- "name_given": the name the client said they have (not a guess from the number)
- "order": what the client ordered or asked to buy/rent/have done, with quantities
- "address": the client's address or delivery/visit location
- "summary": 2-4 short sentences: what the client asked, what was agreed or planned (date/time/price), and anything still open that the owner must follow up on.

Conversation:
"""


def ai_extract(msgs, client_senders, api_key, model):
    lines = []
    for m in msgs:
        if not m.sender:
            continue
        who = "CLIENT" if m.sender in client_senders else "ME"
        lines.append(f"[{m.when:%Y-%m-%d %H:%M}] {who}: {m.text}")
    transcript = "\n".join(lines)
    if len(transcript) > 30000:
        transcript = transcript[-30000:]
    body = json.dumps({
        "model": model,
        "max_tokens": 700,
        "messages": [{"role": "user", "content": AI_PROMPT + transcript}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, method="POST",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode("utf-8"))
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    mm = re.search(r"\{.*\}", text, re.S)
    out = json.loads(mm.group(0)) if mm else {}
    return {
        "name_given": str(out.get("name_given", "")).strip(),
        "order": str(out.get("order", "")).strip(),
        "address": str(out.get("address", "")).strip(),
        "summary": str(out.get("summary", "")).strip(),
        "method": "ai",
    }


# ---------------------------------------------------------------- report

def build_report(export_folder, start, end, my_name="", api_key="", model="claude-sonnet-5-5",
                 default_order="DMY", log=print):
    """start/end are dates (inclusive). Returns a list of chat result dicts."""
    chats = load_exports(export_folder, default_order)
    log(f"Found {len(chats)} exported chat(s).")
    name_to_num, num_to_name = load_contacts(export_folder)
    my_names = {n.strip() for n in my_name.split(",") if n.strip()} or detect_my_names(chats)
    results = []
    for c in chats:
        msgs = [m for m in c["messages"] if start <= m.when.date() <= end]
        if not any(m.sender for m in msgs):
            continue
        who = identify_client(c, my_names, name_to_num, num_to_name)
        info = None
        if api_key:
            try:
                info = ai_extract(msgs, who["client_senders"], api_key, model)
            except Exception as e:  # network, key, quota -> fall back
                log(f"AI summary failed for {c['title']}: {e}. Using basic summary.")
        if info is None:
            info = basic_extract(msgs, who["client_senders"])
        results.append({
            "title": c["title"],
            "number": who["number"],
            "saved_name": who["saved_name"],
            "is_group": who["is_group"],
            "first": min(m.when for m in msgs),
            "last": max(m.when for m in msgs),
            "count": sum(1 for m in msgs if m.sender),
            "client_count": sum(1 for m in msgs if m.sender in who["client_senders"]),
            **info,
        })
        log(f"  {c['title']}: {results[-1]['count']} message(s)")

    def sort_key(r):
        if r["saved_name"]:
            return (1, r["saved_name"].lower())
        return (0, re.sub(r"\D", "", r["number"]) or r["title"])
    results.sort(key=sort_key)
    return results


def display_name(r):
    if r["saved_name"]:
        return r["saved_name"]
    if r["name_given"]:
        return f"{r['name_given']} (named in chat)"
    return r["number"] or r["title"]


def write_report(results, out_folder, start, end):
    os.makedirs(out_folder, exist_ok=True)
    stamp = f"{start:%Y-%m-%d}" if start == end else f"{start:%Y-%m-%d}_to_{end:%Y-%m-%d}"
    html_path = os.path.join(out_folder, f"WhatsApp-Report_{stamp}.html")
    csv_path = os.path.join(out_folder, f"WhatsApp-Report_{stamp}.csv")

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Name", "Number", "Saved name", "Name given in chat", "Order", "Address", "Summary",
                    "First message", "Last message", "Messages"])
        for r in results:
            w.writerow([display_name(r), r["number"] or "unknown", r["saved_name"], r["name_given"], r["order"],
                        r["address"], r["summary"].replace("\n", " "), f"{r['first']:%Y-%m-%d %H:%M}",
                        f"{r['last']:%Y-%m-%d %H:%M}", r["count"]])

    e = html.escape
    period = f"{start:%A %d %B %Y}" if start == end else f"{start:%d %b %Y} – {end:%d %b %Y}"
    cards = []
    for i, r in enumerate(results, 1):
        number = e(r["number"]) if r["number"] else '<span class="muted">number not in export</span>'
        def row(label, val):
            v = e(val).replace("\n", "<br>") if val else '<span class="muted">not mentioned</span>'
            return f"<tr><th>{label}</th><td>{v}</td></tr>"
        tags = []
        if r["is_group"]:
            tags.append('<span class="tag">group</span>')
        if r["client_count"] and r["last"] and r["count"]:
            pass
        cards.append(f"""
<section class="card">
  <div class="head"><span class="idx">{i}</span><h2>{e(display_name(r))}</h2>{''.join(tags)}
    <span class="num">{number}</span></div>
  <table>
    {row("Name given", r["name_given"])}
    {row("Ordered", r["order"])}
    {row("Address", r["address"])}
    {row("Summary", r["summary"])}
  </table>
  <div class="foot">{r['count']} messages · {r['first']:%d-%m %H:%M} – {r['last']:%d-%m %H:%M}
    · {'AI summary' if r['method'] == 'ai' else 'basic summary'}</div>
</section>""")
    if not cards:
        cards.append('<p class="muted">No chats with messages in this period. '
                     'Did you put the exported chats in the export folder?</p>')
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>WhatsApp report {e(stamp)}</title>
<style>
:root {{ --bg:#f4f6f5; --card:#fff; --ink:#17201b; --muted:#6b7570; --line:#e2e7e4; --accent:#128c4a; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111614; --card:#1a211e; --ink:#e7ece9; --muted:#93a09a; --line:#2a332f; --accent:#3ccf7d; }} }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 "Segoe UI", system-ui, sans-serif; }}
main {{ max-width:900px; margin:0 auto; padding:24px 16px 48px; }}
h1 {{ font-size:22px; margin:0 0 4px; }}
.sub {{ color:var(--muted); margin-bottom:20px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; margin-bottom:12px; break-inside:avoid; }}
.head {{ display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }}
.head h2 {{ font-size:17px; margin:0; }}
.idx {{ color:var(--muted); font-variant-numeric:tabular-nums; }}
.num {{ margin-left:auto; font-variant-numeric:tabular-nums; color:var(--accent); font-weight:600; }}
.tag {{ font-size:12px; border:1px solid var(--line); border-radius:99px; padding:0 8px; color:var(--muted); }}
table {{ width:100%; border-collapse:collapse; margin-top:8px; }}
th {{ text-align:left; vertical-align:top; width:110px; color:var(--muted); font-weight:500; padding:3px 8px 3px 0; }}
td {{ padding:3px 0; }}
.muted {{ color:var(--muted); font-style:italic; }}
.foot {{ color:var(--muted); font-size:12px; margin-top:6px; }}
@media print {{ body {{ background:#fff; }} .card {{ border-color:#ccc; }} }}
</style></head><body><main>
<h1>WhatsApp client report</h1>
<div class="sub">{e(period)} · {len(results)} chat(s) · made {dt.datetime.now():%d-%m-%Y %H:%M}</div>
{''.join(cards)}
</main></body></html>"""
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(page)
    return html_path, csv_path
