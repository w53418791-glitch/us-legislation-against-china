#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美国涉华技术立法跟踪台 · 每日自动更新（GitHub Actions 版）
数据源：govinfo BILLSTATUS（无需 key）、Federal Register API（无需 key）
分析：DeepSeek API（可选，缺 key 则跳过 AI 快评）
产物：原地更新 index.html 的 DATA / TRACKER 段，并保持界面铁律。
"""
import os, re, json, sys, html, urllib.request, urllib.error, datetime, time

ROOT = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(os.path.dirname(ROOT), "index.html") if os.path.basename(ROOT) == "scripts" else os.path.join(ROOT, "index.html")
UA = {"User-Agent": "Mozilla/5.0 (compatible; us-legislation-tracker/1.0; +https://github.com/w53418791-glitch)"}
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
NOW = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
TODAY = NOW.strftime("%Y-%m-%d")

# ---------- 法案映射：key -> [(chamber, billnum)] ----------
BILLS = {
    "ndaa":      [("hr", "8800"), ("s", "4784")],
    "match":     [("hr", "8170"), ("s", "4281")],
    "chip":      [("hr", "3447"), ("s", "1705")],
    "overwatch": [("hr", "6875"), ("s", "4456")],
    "rasa":      [("hr", "2683"), ("s", "3519")],
    "theft":     [("hr", "8283")],
    "noaa":      [("hr", "4142"), ("s", "2177")],
    "oss":       [("hr", "10152")],
    "papa":      [("hr", "9142")],
    "fapd":      [("hr", "9143")],
    "ruiran":    [("hr", "5334")],
}

def log(*a):
    print(*a, flush=True)

def fetch(url, timeout=30, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            time.sleep(2 + i * 2)
    raise RuntimeError("fetch failed %s: %s" % (url, last))

# ---------- 1. 抓 govinfo BILLSTATUS ----------
def billstatus_url(ch, num):
    return "https://www.govinfo.gov/bulkdata/BILLSTATUS/119/%s/BILLSTATUS-119%s%s.xml" % (ch, ch, num)

def parse_actions(xml):
    """返回 [(date, text, type)]，按日期倒序"""
    acts = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
        d = re.search(r"<actionDate>(\d{4}-\d{2}-\d{2})</actionDate>", item)
        t = re.search(r"<text>(.*?)</text>", item, re.S)
        ty = re.search(r"<type>(.*?)</type>", item, re.S)
        if d and t:
            txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(t.group(1)))).strip()
            acts.append((d.group(1), txt, (ty.group(1).strip() if ty else "")))
    acts.sort(key=lambda x: x[0], reverse=True)
    return acts

def fetch_all():
    res = {}
    for key, lst in BILLS.items():
        res[key] = {}
        for ch, num in lst:
            try:
                xml = fetch(billstatus_url(ch, num))
                upd = re.search(r"<updateDate>(.*?)</updateDate>", xml)
                res[key]["%s%s" % (ch, num)] = {
                    "actions": parse_actions(xml),
                    "update": upd.group(1)[:10] if upd else "",
                    "ok": True,
                }
                log("  ok %-9s %s%s actions=%d upd=%s" % (key, ch, num, len(res[key]["%s%s" % (ch, num)]["actions"]), res[key]["%s%s" % (ch, num)]["update"]))
            except Exception as e:
                res[key]["%s%s" % (ch, num)] = {"ok": False, "err": str(e)}
                log("  FAIL %s %s%s: %s" % (key, ch, num, e))
    return res

# ---------- 2. 读页面、定位 DATA / TRACKER ----------
def load_index():
    with open(INDEX, encoding="utf-8") as f:
        return f.read()

def extract_js_literal(src, name):
    """花括号配对定位 `const NAME = {...};` / `window.TRACKER={...};` 的 JSON 字面量"""
    if name == "TRACKER":
        m = re.search(r"window\.TRACKER\s*=\s*\{", src)
    else:
        m = re.search(r"const\s+%s\s*=\s*\{" % name, src)
    if not m:
        raise RuntimeError("cannot locate %s" % name)
    start = m.end() - 1
    depth, i, in_str, esc = 0, start, False, False
    while i < len(src):
        ch = src[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return src[start:i + 1], start, i + 1
        i += 1
    raise RuntimeError("unbalanced %s" % name)

def safe_json(raw):
    """容错：页面里用 <b> 等 HTML 片段，JSON 里合法（是字符串内容），直接 loads"""
    return json.loads(raw)

# ---------- 3. 更新逻辑 ----------
STAGE_WORDS = [
    ("introduced", ["Introduced in House", "Introduced in Senate", "Referred to"]),
    ("committee", ["Committee Consideration", "Ordered to be Reported", "Reported by", "Placed on", "Hearings"]),
    ("floor", ["Passed/agreed to in House", "Passed/agreed to in Senate", "On passage", "Motion to proceed", "Cloture"]),
    ("other", ["Received in the Senate", "Received in the House", "Referred to the Committee"]),
    ("president", ["Presented to President", "Signed by President", "Became Public Law"]),
]

def classify(text):
    for stage, kws in STAGE_WORDS:
        for kw in kws:
            if kw.lower() in text.lower():
                return stage
    return ""

def short_action(text, limit=44):
    t = re.sub(r"\s+", " ", text).strip().rstrip(".")
    t = t.replace("Ordered to be Reported in the Nature of a Substitute (Amended) by the Yeas and Nays:", "外委会表决报出")
    t = t.replace("Ordered to be Reported (Amended) by the Yeas and Nays:", "外委会表决报出")
    t = t.replace("Committee Consideration and Mark-up Session Held", "委员会完成标记")
    t = t.replace("Referred to the House Committee on Foreign Affairs", "转众院外委会")
    t = t.replace("Referred to the Committee on Banking, Housing, and Urban Affairs", "转参院银行委员会")
    t = t.replace("Referred to the Committee on the Judiciary", "转司法委员会")
    t = t.replace("Referred to the House Committee on the Judiciary", "转众院司法委员会")
    t = t.replace("Read twice and referred to", "两读转")
    t = re.sub(r"Introduced in (House|Senate)", lambda m: "众院提出" if m.group(1)=="House" else "参院提出", t)
    t = re.sub(r"Referred to the House Committee on Armed Services", "转众院军委会", t)
    t = t.replace("Referred to the House Committee on", "转众院")
    t = t.replace("Received in the Senate", "参院收到")
    t = t.replace("Received in the House", "众院收到")
    if len(t) > limit:
        t = t[:limit] + "…"
    return t

# 只认这些“有里程碑意义”的动作，其余程序性动作一律不收
MILESTONE = [
    ("committee_markup", ["ordered to be reported", "reported by", "committee consideration and mark-up"]),
    ("house_pass",       ["passed/agreed to in house"]),
    ("senate_pass",      ["passed/agreed to in senate"]),
    ("to_other_chamber", ["received in the senate", "received in the house"]),
    ("president",        ["presented to president", "signed by president", "became public law"]),
    ("floor",            ["cloture on the motion to proceed", "motion to proceed to consider"]),
]

def milestone_of(txt):
    low = txt.lower()
    for name, kws in MILESTONE:
        for kw in kws:
            if kw in low:
                return name
    return ""

def cn_action(txt, limit=38):
    """把英文官方动作转成简短中文表述"""
    low = txt.lower()
    if "ordered to be reported" in low or low.startswith("reported by"):
        m = re.search(r"(\d+)\s*-\s*(\d+)", txt)
        score = (" %s-%s" % (m.group(1), m.group(2))) if m else ""
        return "外委会表决报出" + score
    if "passed/agreed to in house" in low:
        m = re.search(r"(\d+)\s*-\s*(\d+)", txt)
        return "众院全院通过" + ((" %s-%s" % (m.group(1), m.group(2))) if m else "")
    if "passed/agreed to in senate" in low:
        m = re.search(r"(\d+)\s*-\s*(\d+)", txt)
        return "参院全院通过" + ((" %s-%s" % (m.group(1), m.group(2))) if m else "")
    if "received in the senate" in low:
        return "参院收到"
    if "received in the house" in low:
        return "众院收到"
    if "signed by president" in low or "became public law" in low:
        return "总统签署成法"
    if "presented to president" in low:
        return "送交总统签署"
    if "cloture" in low:
        return "程序性表决（cloture）"
    if "motion to proceed" in low:
        return "动议进入审议"
    return txt[:limit]

def norm_date(d):
    """把 09-22 / 2026-09-22 / 2026-09-22T.. 统一为 YYYY-MM-DD"""
    d = str(d).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", d)
    if m:
        return "%s-%s-%s" % (m.group(1), m.group(2), m.group(3))
    m = re.match(r"^(\d{2})-(\d{2})$", d)
    if m:
        return "%d-%s-%s" % (NOW.year, m.group(1), m.group(2))
    return d

def upsert_timeline(obj, fetched):
    """只追加晚于页面最新日期、且有里程碑意义的官方动作；每个编号独立判断"""
    if not isinstance(obj.get("tl"), list):
        return 0
    tl = obj["tl"]
    existing = [norm_date(r[0]) for r in tl if isinstance(r, list) and r]
    last_date = max(existing) if existing else "1900-01-01"
    have = set(existing)
    added = 0
    for bill, info in fetched.items():
        if not info.get("ok"):
            continue
        best = {}
        for d, txt, ty in info["actions"]:
            d = norm_date(d)
            if d <= last_date or d in have:
                continue
            if not milestone_of(txt):
                continue
            if d not in best or len(txt) > len(best[d]):
                best[d] = txt
        for d in sorted(best):
            label = "[%s] %s" % (bill.upper(), cn_action(best[d]))
            tl.append([d, label])
            have.add(d)
            added += 1
            log("    + tl %-9s %s | %s" % (obj.get("key"), d, label))
    tl.sort(key=lambda x: str(x[0]))
    return added

def deepseek(prompt, system):
    if not DEEPSEEK_KEY:
        return ""
    body = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "temperature": 0.6, "max_tokens": 400,
    }).encode()
    req = urllib.request.Request("https://api.deepseek.com/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + DEEPSEEK_KEY})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            js = json.loads(r.read().decode())
        return js["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log("  deepseek fail:", e)
        return ""

def shrink_ai(text, lo=120, hi=180):
    t = re.sub(r"[#*`>]", "", text)
    t = re.sub(r"\s+", "", t)
    return t[:hi] if len(t) > hi else t

def main():
    log("== run", NOW.isoformat(), "==")
    src = load_index()
    raw_data, ds, de = extract_js_literal(src, "DATA")
    raw_tr, ts_, te = extract_js_literal(src, "TRACKER")
    tdata = safe_json(raw_data)
    ttr = safe_json(raw_tr)

    log("[1] fetching official actions")
    fetched = fetch_all()

    changed = []
    log("[2] merging")
    targets = []
    if isinstance(tdata.get("bills"), list):
        targets += [b for b in tdata["bills"] if isinstance(b.get("tl"), list)]
    if isinstance(tdata.get("ext"), list):
        targets += [b for b in tdata["ext"] if isinstance(b.get("tl"), list)]
    # DATA.ndaa 是静态卡片（时间线写在 HTML 里），不参与 tl 合并

    for obj in targets:
        key = obj.get("key")
        if key not in fetched:
            continue
        n = upsert_timeline(obj, fetched[key])
        if n:
            changed.append((key, n, obj))

    log("[3] AI commentary for changed bills: %d" % len(changed))
    for key, n, obj in changed:
        prompt = ("法案：%s\n要点：%s\n最新进展：%s\n请写一段 120~180 字中文快评，"
                  "只讲判断与影响，不要列举信源，不要用列表，不要加标题。" %
                  (obj.get("zh", key), re.sub(r"<[^>]+>", "", obj.get("point", ""))[:300],
                   re.sub(r"<[^>]+>", "", obj.get("latest", ""))[:300]))
        ai = deepseek(prompt, "你是中美科技与出口管制政策分析专家，语言凝练、有判断、不堆砌事实。")
        if ai:
            obj["ai"] = shrink_ai(ai)
            obj["aiTs"] = TODAY
            log("    ai ok %s (%d chars)" % (key, len(obj["ai"])))

    # 更新 stamp / 页眉日期（ensure_ascii=False + 紧凑分隔符，避免体积膨胀）
    ttr["stamp"] = TODAY
    dumps = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))
    # 先替换 TRACKER（位置靠后），再替换 DATA（位置靠前），避免 offset 失效
    src = src[:ts_] + dumps(ttr) + src[te:]
    raw_data3, ds3, de3 = extract_js_literal(src, "DATA")
    src = src[:ds3] + dumps(tdata) + src[de3:]

    src = re.sub(r"<b>数据截至：</b>[^<]*（GMT\+8）", "<b>数据截至：</b>%s（GMT+8）" % TODAY, src)
    src = re.sub(r'<div class="footer">[^<]*</div>', '<div class="footer">美国涉华技术立法跟踪 · 更新于 %s</div>' % TODAY, src)

    # 界面铁律自检
    assert src.count("<b>信源") == 0, "界面铁律：不得出现信源块"
    assert 'class="refs"' not in src, "界面铁律：refs 不得渲染"

    with open(INDEX, "w", encoding="utf-8") as f:
        f.write(src)
    log("[4] written. changed=%d" % len(changed))
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_summary.txt"), "w", encoding="utf-8") as f:
        f.write("date=%s changed=%s\n" % (TODAY, ",".join("%s(+%d)" % (k, n) for k, n, _ in changed) or "none"))

if __name__ == "__main__":
    main()
