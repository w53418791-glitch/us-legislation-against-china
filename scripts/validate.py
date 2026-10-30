# -*- coding: utf-8 -*-
"""index.html 交付前校验：结构闭合 + 界面铁律 + 数据完整性"""
import re, sys, json
from html.parser import HTMLParser

PATH = "index.html"

class P(HTMLParser):
    def __init__(self):
        super().__init__(); self.stack = []; self.errs = []
        self.void = {'meta', 'br', 'img', 'hr', 'input', 'link', 'source'}
    def handle_starttag(self, t, a):
        if t not in self.void:
            self.stack.append(t)
    def handle_endtag(self, t):
        if t in self.void:
            return
        if self.stack and self.stack[-1] == t:
            self.stack.pop()
        else:
            self.errs.append(t)

def brace_literal(src, keyword):
    m = re.search(keyword, src)
    if not m:
        raise SystemExit("找不到数据段: %s" % keyword)
    start = m.end() - 1
    depth, i, in_str, esc = 0, start, False, False
    while i < len(src):
        ch = src[i]
        if in_str:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': in_str = False
        else:
            if ch == '"': in_str = True
            elif ch == "{": depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(src[start:i + 1])
        i += 1
    raise SystemExit("数据段花括号不闭合: %s" % keyword)

def main():
    h = open(PATH, encoding="utf-8").read()
    errs = []

    p = P(); p.feed(h)
    if p.errs or p.stack:
        errs.append("HTML 标签不闭合: %s %s" % (p.errs[:3], p.stack[:3]))

    if h.count("<b>信源") != 0:
        errs.append("界面铁律：不得出现信源块")
    if 'class="refs"' in h:
        errs.append("界面铁律：refs 不得渲染")
    m = re.search(r'<div class="meta-line">(.*?)</div>', h, re.S)
    if not m or "信源" in m.group(1) or "跟踪：" in m.group(1):
        errs.append("界面铁律：页眉不得含信源清单")

    d = brace_literal(h, r"const\s+DATA\s*=\s*\{")
    t = brace_literal(h, r"window\.TRACKER\s*=\s*\{")

    if len(d.get("bills", [])) != 9:
        errs.append("DATA.bills 应为 9，实际 %d" % len(d.get("bills", [])))
    if len(d.get("ext", [])) != 1:
        errs.append("DATA.ext 应为 1，实际 %d" % len(d.get("ext", [])))
    if len(t.get("feed", [])) > 20:
        errs.append("feed 不得超过 20 条，实际 %d" % len(t["feed"]))
    ts = [x.get("ts", "") for x in t.get("feed", [])]
    if ts != sorted(ts, reverse=True):
        errs.append("feed 必须按时间倒序")

    if errs:
        print("❌ 校验未通过：")
        for e in errs:
            print("  -", e)
        sys.exit(1)

    print("✅ 校验通过：bills=%d ext=%d feed=%d" %
          (len(d["bills"]), len(d["ext"]), len(t["feed"])))

if __name__ == "__main__":
    main()
