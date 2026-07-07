"""MVP Insights dashboard — pull the LAST 7 DAYS of every published signal type (news / digest / signal /
onchain / heatmap / sentiment) from the /published endpoint and render the gold-themed single-file
dashboard (tabs + cards + detail modal + share bar) that matches the post-card design.

Pipeline:  /published (all sources) → filter last 7d → transform to the `viewdata` schema →
inject into dashboard_template.html → signals_7d.html (self-contained, offline-openable).

The card IMAGES are the REAL rendered cards the pipeline already hosts (GCS URLs in each item's `image`);
text-only items (news / heatmap / sentiment) render text-only in the modal. NOTHING is synthesized — all
text/numbers come from the endpoint. Heatmap/sentiment are rich nested objects (`item['signal']`), the rest
are flat feed items whose `message` we parse into emoji-headed sections.

Usage:  py scripts/build_dashboard.py [out.html] [--days 7] [--asof YYYY-MM-DD]
"""
import sys, os, re, json, html, urllib.request
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__)); SKILL = os.path.dirname(HERE)
BASE = "https://news-api-1094890588015.asia-southeast1.run.app/published"
TEMPLATE = os.path.join(SKILL, "dashboard_template.html")
DEFAULT_SHARE = "https://gfi.io"   # share target when an item has no source_url and no card image

TABS = ["Tất cả", "Bản tin 06:30", "Bản tin 13:00", "Bản tin 19:00", "Tin nóng",
        "Trước sự kiện", "Lịch tuần", "On-chain Insight", "Whales Alert",
        "Cấu trúc thị trường", "Sector & Narrative"]

# ---------- helpers ----------------------------------------------------------
def parse_ts(t):
    if not t: return None
    try: return datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    except Exception: return None

def trunc(s, n=180):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n].rstrip() + "…"

def is_header(line):
    """A message section header line starts with a pictograph emoji — but NOT a coloured bullet
    circle/square (🟢🔴🟡… are inline bullets) nor '•'/'-'."""
    s = line.lstrip()
    if not s: return False
    o = ord(s[0])
    if o < 0x1F000: return False                 # ascii, '•'(0x2022), arrows → body
    if 0x1F7E0 <= o <= 0x1F7EB: return False      # 🟢🔴🟡🔵🟠🟣⚫⚪ → inline bullets
    return True

def clean_title(lbl):
    """Drop the leading emoji + separators from a header to get a plain title."""
    i = 0
    while i < len(lbl) and (ord(lbl[i]) >= 0x1F000 or lbl[i] in " \t·—–-:"): i += 1
    return lbl[i:].strip(" :·—–-")

def parse_message(msg):
    """Split a feed `message` into [{label, body}] sections by emoji-headed lines. Text before the
    first header becomes an implicit 'Nội dung' section."""
    secs, cur, pre = [], None, []
    for raw in (msg or "").split("\n"):
        if is_header(raw):
            cur = {"label": raw.strip(), "body": []}; secs.append(cur)
        else:
            (cur["body"] if cur else pre).append(raw)
    if pre and pre != [""] and "".join(pre).strip():
        secs.insert(0, {"label": "Nội dung", "body": pre})
    for s in secs:
        s["body"] = "\n".join(s["body"]).strip()
    return [s for s in secs if s["body"] or s["label"]]

def first_body_line(secs):
    for s in secs:
        for ln in s["body"].split("\n"):
            t = re.sub(r"^[🟢🔴⚪🟡🔵🟠🟣•\-\s]+", "", ln).strip()
            if t: return t
    return ""

# ---------- tab / badge classification --------------------------------------
def classify(src, st, title, msg):
    if src == "news" or st == "hack_alert":
        return "Tin nóng", "Tin nóng", "Tin tức khẩn", "red", "Tin nóng"
    if src == "digest":
        low = (title or "").lower(); m = msg or ""
        if "morning" in low or "SÁNG" in m:
            tab, lab = "Bản tin 06:30", "Bản tin sáng"
        elif "noon" in low or "GIỮA NGÀY" in m or "TRƯA" in m:
            tab, lab = "Bản tin 13:00", "Bản tin trưa"
        else:
            tab, lab = "Bản tin 19:00", "Bản tin tối"
        return tab, lab, "Bản tin", "green", lab
    if src == "signal":
        if st in ("wave_recap", "recap_7d", "recap_30d"):
            return "On-chain Insight", "Smart Money", "On-chain", "blue", "On-chain Insight"
        return "Whales Alert", "Smart Money", "Whale", "gold", "Whales Alert"     # open/reduce/close
    if src == "onchain":
        if st in ("cex_outflow", "cex_inflow_spike", "fresh_wallet_inflow", "smart_money_selling"):
            return "Whales Alert", "On-chain", "Whale", "gold", "Whales Alert"
        return "On-chain Insight", "On-chain", "On-chain", "blue", "On-chain Insight"
    if src == "heatmap":
        return "Cấu trúc thị trường", "Market Heatmap", "Cấu trúc thị trường", "purple", "Cấu trúc thị trường"
    if src == "sentiment":
        return "Cấu trúc thị trường", "Market Sentiment", "Market Sentiment", "purple", "Cấu trúc thị trường"
    return "Tất cả", src, src, "gold", src

# ---------- rich builders (heatmap / sentiment) -----------------------------
def pct(v):
    return f"{v:+.1f}%" if isinstance(v, (int, float)) else ""

def build_heatmap(sig, date):
    mk = sig.get("market", {})
    def cats(rows, ico, n):
        out = []
        for e in (rows or [])[:n]:
            line = f"{ico} {e.get('category', '?')} · {pct(e.get('priceChange24h'))} (24h)"
            note = e.get("note")
            out.append(line + (f"\n   {note}" if note else ""))
        return "\n".join(out)
    angles = "\n".join("• " + a for a in (sig.get("contentAngles") or []))
    secs = [
        {"label": "🧠 PHÂN TÍCH TỔNG QUAN", "body": sig.get("analysis", "")},
        {"label": "🔥 NHÓM NÓNG (HOT)", "body": cats(mk.get("hot"), "🔥", 14)},
        {"label": "🧊 NHÓM NGUỘI (COLD)", "body": cats(mk.get("cold"), "🧊", 10)},
    ]
    oc = sig.get("onchain", {})
    if oc.get("hot"):
        secs.append({"label": "⛓️ ONCHAIN NỔI BẬT (DOANH THU)", "body": cats(oc.get("hot"), "🔥", 8)})
    if angles:
        secs.append({"label": "💡 GÓC NỘI DUNG", "body": angles})
    return "Bản đồ nhiệt thị trường", trunc(sig.get("analysis", "")), secs   # date already in the card h3

def build_sentiment(sig, date):
    fg = sig.get("fearGreed", {}) or {}
    hot = "\n".join("• " + t for t in (sig.get("hotTopics") or []))
    angles = "\n".join("• " + a for a in (sig.get("contentAngles") or []))
    secs = [
        {"label": "😨 CHỈ SỐ FEAR & GREED", "body": f"{fg.get('value','?')} — {fg.get('label','')}"},
        {"label": "🧠 PHÂN TÍCH TỔNG QUAN", "body": sig.get("analysis", "")},
        {"label": "🔥 CHỦ ĐỀ THẢO LUẬN NỔI BẬT", "body": hot},
    ]
    if angles:
        secs.append({"label": "💡 GÓC NỘI DUNG", "body": angles})
    title = f"Market Sentiment — Fear & Greed {fg.get('value','?')} · {fg.get('label','')}"
    return title, trunc(sig.get("analysis", "")), secs

# ---------- per-item transform ----------------------------------------------
def transform(x, now):
    src = x.get("source")
    tstr = x.get("published_ts") or x.get("ts") or x.get("generatedAt")
    p = parse_ts(tstr)
    is_new = bool(p and (now - p.astimezone(timezone.utc)) <= timedelta(hours=24))

    if src in ("heatmap", "sentiment"):
        sig = x.get("signal", {}) or {}
        date = sig.get("date", "") or ""
        if re.match(r"^\d{2}/\d{2}$", date): date = f"{date}/{now.year}"
        tab, label, badge, bcls, pill = classify(src, None, "", "")
        if src == "heatmap":
            card_title, summary, secs = build_heatmap(sig, date)
        else:
            card_title, summary, secs = build_sentiment(sig, date)
        return {"image": "", "images": [], "date": date, "source_url": "", "ticker": "",
                "shareUrl": DEFAULT_SHARE,
                "tab": tab, "label": label, "badge": badge, "badgeClass": bcls, "isNew": is_new,
                "pill": pill, "pillClass": bcls, "cardTitle": card_title, "summary": summary,
                "sections": secs, "_ts": p}

    # flat feed item
    msg = x.get("message", "") or ""
    title = x.get("title", "") or ""
    st = x.get("signal_type")
    tab, label, badge, bcls, pill = classify(src, st, title, msg)
    secs = parse_message(msg)
    if src == "signal" and title:
        card_title = title                       # whale titles are clean VN headlines
    elif src == "onchain" and title:
        card_title = title
    elif src == "news":
        card_title = first_body_line(secs) or clean_title(secs[0]["label"]) if secs else title
    elif src == "digest" and secs:
        card_title = clean_title(secs[0]["label"])
    else:
        card_title = title or (clean_title(secs[0]["label"]) if secs else "")
    summary = trunc(first_body_line(secs) or clean_title(secs[0]["label"]) if secs else "")
    img = x.get("image") or ""
    img = img if isinstance(img, str) and img.startswith("http") else ""
    date = p.strftime("%H:%M %d/%m/%Y") if p else ""
    src_url = x.get("source_url") or ""
    return {"image": img, "images": [img] if img else [], "date": date,
            "source_url": src_url, "ticker": x.get("ticker") or "",
            "shareUrl": src_url or img or DEFAULT_SHARE,   # article → card image → default
            "tab": tab, "label": label, "badge": badge, "badgeClass": bcls, "isNew": is_new,
            "pill": pill, "pillClass": bcls, "cardTitle": card_title, "summary": summary,
            "sections": secs, "_ts": p}

# ---------- main -------------------------------------------------------------
def main():
    args = sys.argv[1:]
    out = next((a for a in args if not a.startswith("--")), "signals_7d.html")
    days = int(args[args.index("--days") + 1]) if "--days" in args else 7
    if "--asof" in args:
        now = datetime.fromisoformat(args[args.index("--asof") + 1]).replace(tzinfo=timezone.utc)
    else:
        now = datetime.now(timezone.utc)
    cut = now - timedelta(days=days)

    raw = json.loads(urllib.request.urlopen(BASE, timeout=60).read().decode("utf-8"))
    items = []
    for x in raw:
        t = parse_ts(x.get("published_ts") or x.get("ts") or x.get("generatedAt"))
        if t and t.astimezone(timezone.utc) >= cut:
            items.append(x)

    sigs = [transform(x, now) for x in items]
    sigs.sort(key=lambda s: s["_ts"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    for s in sigs: s.pop("_ts", None)

    counts = {t: 0 for t in TABS}
    counts["Tất cả"] = len(sigs)
    for s in sigs:
        counts[s["tab"]] = counts.get(s["tab"], 0) + 1

    meta = (f"7 ngày gần nhất · {len(sigs)} tín hiệu · cập nhật "
            f"{now.astimezone(timezone(timedelta(hours=7))).strftime('%H:%M %d/%m/%Y')} (giờ VN)")
    viewdata = {"tabs": TABS, "counts": counts, "meta": meta, "signals": sigs}

    tpl = open(TEMPLATE, encoding="utf-8").read()
    payload = json.dumps(viewdata, ensure_ascii=False).replace("</script>", "<\\/script>")
    open(out, "w", encoding="utf-8").write(tpl.replace("__VIEWDATA__", payload))

    print(f"wrote {out}: {len(sigs)} signals (last {days}d)")
    for t in TABS:
        if counts.get(t): print(f"  {counts[t]:>3}  {t}")

if __name__ == "__main__":
    main()
