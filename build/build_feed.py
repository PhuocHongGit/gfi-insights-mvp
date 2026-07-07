"""Insights FEED — the dashboard as a VERTICAL STACK OF POST CARDS (chart-left + gold text-right + share),
one per signal, matching the sample post-card look. Reuses the pipeline's already-hosted chart images
(GCS URLs in each /published item) so NO chart is regenerated; the text panel (headline / tags / lead /
▸ bullets / 💡 tip) is derived from the item's real `message` (or the rich heatmap/sentiment object).

Shares the fetch + parse + tab classification with build_dashboard.py. Output = signals_feed.html
(self-contained; card images load from GCS → needs internet, like the list dashboard).

Usage:  py scripts/build_feed.py [out.html] [--days 7] [--asof YYYY-MM-DD]
"""
import sys, os, re, json, base64, urllib.request
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import build_dashboard as B          # BASE, TABS, DEFAULT_SHARE, parse_ts, parse_message, classify, …
SKILL = os.path.dirname(HERE)
TEMPLATE = os.path.join(SKILL, "feed_template.html")

# status/type tag → (text, bg, fg). Ticker chip is added separately.
STATE = {"open": ("MỞ", "#2EBD85", "#fff"), "close": ("ĐÓNG", "#E5484D", "#fff"),
         "reduce": ("CHỐT DẦN", "#F5A623", "#111")}
DIM = "#2A2C34"; DIMFG = "#D7DBE3"
CITE = re.compile(r"\s*\[\d+\](?:\s*\[\d+\])*")          # inline citation markers: [1] , [3][4] , [6][7][8][9]

def strip_cites(s):
    """Drop the raw [N] citation markers so the card text reads clean (sources stay in the modal)."""
    return CITE.sub("", s or "").strip()

def is_sources(lbl):
    u = (lbl or "").upper()
    return "NGUỒN" in u or lbl.startswith("📰")

def lead_bullets_tip(sections):
    """Split parsed message sections into a post-card lead + ▸ bullets + 💡 tip."""
    lead, bullets, tip = "", [], ""
    for sec in sections:
        lbl, body = sec["label"], sec["body"]
        if lbl.startswith("💡") or "GỢI Ý" in lbl.upper():
            tip = (body.split("\n")[0].strip() if body else B.clean_title(lbl))
            continue
        if is_sources(lbl):                              # the 📰 Nguồn source list is NOT lead/bullet material
            continue
        for ln in body.split("\n"):
            s = ln.strip()
            if not s:
                continue
            o = ord(s[0])
            # bullets: -, •, coloured circles 🟢🔴🟡🔵🟠🟣 (1F3xx–1FAxx / 1F7Ex) AND the neutral ⚪⚫ (U+26AA/AB)
            is_bullet = s[0] in "-•⚪⚫◽◾▪▫" or (0x1F300 <= o <= 0x1FAFF) or (0x1F7E0 <= o <= 0x1F7EB)
            if is_bullet:
                b = re.sub(r"^[-•\s]+", "", s)
                b = re.sub(r"^[\U0001F000-\U0001FAFF☀-➿\s·]+", "", b).strip()
                if b:
                    bullets.append(b)
            elif not lead:
                lead = s
    return lead, bullets[:5], tip

def tags_for(src, st, ticker, badge):
    if src == "signal" and st in STATE:
        t = STATE[st]; tag0 = {"text": t[0], "bg": t[1], "fg": t[2]}
    elif src == "signal" and st in ("wave_recap", "recap_7d", "recap_30d"):
        tag0 = {"text": "SM · 8H" if st == "wave_recap" else "SM RECAP", "bg": DIM, "fg": DIMFG}
    elif src == "onchain":
        tag0 = {"text": (badge or "ON-CHAIN").upper(), "bg": "#3A6FF0", "fg": "#fff"}
    elif src == "news":
        tag0 = {"text": "TIN NÓNG", "bg": "#E5484D", "fg": "#fff"}
    elif src == "digest":
        tag0 = {"text": "BẢN TIN", "bg": "#23A26B", "fg": "#fff"}
    else:  # heatmap / sentiment
        tag0 = {"text": "THỊ TRƯỜNG", "bg": "#8E5BD6", "fg": "#fff"}
    tags = [tag0]
    if ticker:
        tags.append({"text": "$" + ticker, "bg": DIM, "fg": DIMFG})
    return tags

def to_card(base, raw):
    """base = build_dashboard.transform() dict; raw = the original /published item."""
    src = raw.get("source"); st = raw.get("signal_type")
    lead, bullets, tip = lead_bullets_tip(base.get("sections", []))
    if not lead:
        lead = base.get("summary", "")
    lead, tip = strip_cites(lead), strip_cites(tip)
    bullets = [b for b in (strip_cites(b) for b in bullets) if b and b != lead][:5]   # clean + drop lead dup
    sections = [{"label": s["label"], "body": s["body"] if is_sources(s["label"]) else CITE.sub("", s["body"])}
                for s in base.get("sections", [])]     # clean cites for the modal, keep the 📰 Nguồn list + links
    return {
        "tab": base["tab"], "date": base["date"], "isNew": base["isNew"],
        "headline": (base["cardTitle"] or base["label"]).upper(),
        "title": base.get("cardTitle", "") or base.get("label", ""),
        "tags": tags_for(src, st, base.get("ticker", ""), base.get("badge", "")),
        "lead": lead, "bullets": bullets, "tip": tip,
        "sections": sections,                       # full detail for the click-to-open modal (cites cleaned)
        "cardImg": base.get("image", ""), "shareUrl": base.get("shareUrl", B.DEFAULT_SHARE),
        "brand": "GFI · SMART MONEY" if src in ("signal",) else "GFI · INSIGHTS",
        "_ts": base.get("_ts"),
    }

def data_uri(path):
    ext = os.path.splitext(path)[1].lower()
    mime = "image/png" if ext == ".png" else ("image/jpeg" if ext in (".jpg", ".jpeg") else "application/octet-stream")
    return f"data:{mime};base64," + base64.b64encode(open(path, "rb").read()).decode()

def rel_time(ts, now):
    """Vietnamese relative timestamp, computed at build time (refreshes each rebuild)."""
    if not ts: return ""
    s = (now - ts.astimezone(timezone.utc)).total_seconds()
    if s < 0: s = 0
    if s < 60: return "vừa xong"
    if s < 3600: return f"{int(s // 60)} phút trước"
    if s < 86400: return f"{int(s // 3600)} giờ trước"
    if s < 7 * 86400: return f"{int(s // 86400)} ngày trước"
    if s < 30 * 86400: return f"{int(s // (7 * 86400))} tuần trước"
    if s < 365 * 86400: return f"{int(s // (30 * 86400))} tháng trước"
    return f"{int(s // (365 * 86400))} năm trước"

def local_extra_cards(local_json, assets_dir, now, cut):
    """Cards the API doesn't serve, pulled from the local snapshot json. EVERY one becomes a compact
    chart-left / text-right POST CARD (user 2026-07-06 "merge similar to previous"): the item's own RICH
    rendered card (`image` = a bare filename present in `--assets`) is the LEFT wildcard, the reconstructed
    text sits on the RIGHT. Covers news (Breaking_news *.png), heatmap (narrative), sentiment (fear-greed).
    Embedded as data-URIs (self-contained). Window-filtered here (drops once the snapshot ages past `cut`),
    and each card is tagged `_src` so the caller knows which live sources it should suppress as duplicates."""
    try:
        items = json.load(open(local_json, encoding="utf-8"))
    except Exception as e:
        print(f"  ! could not read local snapshot {local_json}: {e}"); return []
    out = []
    for x in items:
        src = x.get("source")
        img = x.get("image") or ""
        p = os.path.join(assets_dir, img) if img and not img.startswith("http") else ""
        if not (p and os.path.exists(p)):
            continue                                # only items whose rich card we actually have locally
        base = B.transform(x, now)
        t = base.get("_ts")
        if t and t.astimezone(timezone.utc) < cut:  # snapshot aged out of the window → drop (self-cleaning)
            continue
        c = to_card(base, x)
        c["cardImg"] = data_uri(p); c["fullCard"] = False; c["_src"] = src
        if src == "news":                           # news lead = the article DETAIL, not the repeated headline
            secs = base.get("sections") or []
            if secs:
                lines = [l for l in secs[0]["body"].split("\n") if l.strip()]
                if len(lines) > 1:
                    c["lead"] = strip_cites(lines[1])
        out.append(c)
    return out

def main():
    args = sys.argv[1:]
    out = next((a for a in args if not a.startswith("--")), "signals_feed.html")
    days = int(args[args.index("--days") + 1]) if "--days" in args else 7
    now = (datetime.fromisoformat(args[args.index("--asof") + 1]).replace(tzinfo=timezone.utc)
           if "--asof" in args else datetime.now(timezone.utc))
    local = args[args.index("--local") + 1] if "--local" in args else None
    assets = args[args.index("--assets") + 1] if "--assets" in args else (os.path.dirname(local) if local else None)
    demo = args[args.index("--demo") + 1] if "--demo" in args else None
    cut = now - timedelta(days=days)

    # local rich cards first — so we know which live sources (heatmap/sentiment) they cover this run.
    extras = local_extra_cards(local, assets, now, cut) if local else []
    covers = {e.get("_src") for e in extras} & {"heatmap", "sentiment"}

    raw = json.loads(urllib.request.urlopen(B.BASE, timeout=60).read().decode("utf-8"))
    cards = []
    for x in raw:
        t = B.parse_ts(x.get("published_ts") or x.get("ts") or x.get("generatedAt"))
        if not (t and t.astimezone(timezone.utc) >= cut):
            continue
        # suppress the live text-only heatmap/sentiment ONLY when an in-window local RICH card covers it;
        # once the snapshot ages out (covers empties) the live ones show again → no gap for a 24h auto-build.
        if x.get("source") in covers:
            continue
        base = B.transform(x, now)                 # reuse the dashboard transform (sections, tab, image…)
        cards.append(to_card(base, x))

    if extras:                                     # merge the local rich cards, deduped by (headline, date)
        seen = {(c.get("headline"), c.get("date")) for c in cards}
        merged = 0
        for e in extras:
            key = (e.get("headline"), e.get("date"))
            if key in seen:
                continue
            cards.append(e); seen.add(key); merged += 1
        print(f"  + merged {merged} local card(s) from {os.path.basename(local)} (chart-left rich wildcard + text)")

    if demo:                                        # DEMO posts (e.g. the 'Lịch tuần' weekly unlock cards)
        try:
            dc = json.load(open(demo, encoding="utf-8"))
            for d in dc:
                img = d.pop("image", None)
                if img:
                    p = os.path.join(assets or os.path.dirname(demo), img)
                    if os.path.exists(p): d["cardImg"] = data_uri(p)
                d.setdefault("fullCard", False)
                d["_ts"] = B.parse_ts(d.pop("ts", None))   # timestamp = the week's Monday; kept for the merged sort
                d.setdefault("_src", None)
            cards += dc
            print(f"  + injected {len(dc)} demo post(s) from {os.path.basename(demo)}")
        except Exception as e:
            print(f"  ! demo inject failed: {e}")

    # order the WHOLE feed ('Tất cả') by timestamp, newest first — demo posts sort into their real positions
    cards.sort(key=lambda c: c.get("_ts") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    for c in cards:
        c["ago"] = rel_time(c.get("_ts"), now)
        c.pop("_ts", None); c.pop("_src", None)

    counts = {t: 0 for t in B.TABS}; counts["Tất cả"] = len(cards)
    for c in cards: counts[c["tab"]] = counts.get(c["tab"], 0) + 1
    meta = (f"7 ngày gần nhất · {len(cards)} tín hiệu · cập nhật "
            f"{now.astimezone(timezone(timedelta(hours=7))).strftime('%H:%M %d/%m/%Y')} (giờ VN)")
    viewdata = {"tabs": B.TABS, "counts": counts, "meta": meta, "signals": cards}

    tpl = open(TEMPLATE, encoding="utf-8").read()
    payload = json.dumps(viewdata, ensure_ascii=False).replace("</script>", "<\\/script>")
    open(out, "w", encoding="utf-8").write(tpl.replace("__VIEWDATA__", payload))
    print(f"wrote {out}: {len(cards)} post cards (last {days}d)")
    for t in B.TABS:
        if counts.get(t): print(f"  {counts[t]:>3}  {t}")

if __name__ == "__main__":
    main()
