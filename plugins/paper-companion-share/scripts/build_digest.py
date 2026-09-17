#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
paper-digest · 解析版全集生成器（单文件响应式 HTML）

定位：paper-companion 研读产出的「最终交付层」。工作流内部照旧生成 Markdown
（index.md / papers/<标题>/{overview,section-guides,notes,citations,background,
translation}.md），本脚本把全部产出**合并渲染成一个大的自包含 HTML**，访问更方便：

  · 侧边目录 + 锚点 + 滚动高亮（桌面固定 / 手机抽屉）
  · 每篇论文一个区块：重要度徽章 / 一句话概览 / 各模块分节 / 完整转写默认折叠
  · 客户端全文搜索（按论文过滤，显示命中数）
  · ★ 本地文件索引：工作区根目录 PDF（用户原文）+ .paper-companion 下所有 PDF
    + 已生成的 library 门户，全部以相对路径可点击——这是对「解析版必须能索引
    本地文件」这一硬需求的落实（纯在线范例做不到的部分）
  · 单文件自包含：CSS/JS 内联、零依赖、离线可开、<meta charset utf-8> + viewport
  · 相对路径引用本地 PDF，因此把「工作区根目录」整目录部署（pinme upload）即可
    在公网同时访问解析版与原文 PDF

用法：
  python build_digest.py --workspace "<工作区路径>" [--title 标题] [--out 路径]
                         [--deploy-url https://... ]

默认输出：<workspace>/.paper-companion/digest.html
绝不幻觉：只渲染磁盘上真实存在的 Markdown 与 PDF；缺文件如实显示。
"""

import os
import re
import sys
import json
import time
import argparse
import urllib.parse

# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def esc(s):
    return ("" if s is None else str(s)).replace(
        "&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace(
        '"', "&quot;")


def norm_key(s):
    """标题归一化：小写、只保留字母数字与 CJK"""
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (s or "").lower())


def human_size(n):
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("{:.0f} {}".format(n, unit) if unit == "B"
                    else "{:.1f} {}".format(n, unit))
        n /= 1024.0
    return "{} B".format(n)


def html_rel(src_dir, target):
    """target 相对 src_dir 的 HTML href（正斜杠 + URL 编码 CJK）"""
    rel = os.path.relpath(target, src_dir).replace(os.sep, "/")
    return urllib.parse.quote(rel)


class SlugPool(object):
    def __init__(self):
        self.used = set()

    def make(self, text, prefix=""):
        t = re.sub(r"<[^>]+>", "", text or "")
        t = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", t.strip().lower())
        t = re.sub(r"-+", "-", t).strip("-") or "sec"
        base, k, final = prefix + t, 2, prefix + t
        while final in self.used:
            final = "{}-{}".format(base, k)
            k += 1
        self.used.add(final)
        return final


# --------------------------------------------------------------------------
# Markdown → HTML（轻量渲染器，覆盖研读笔记的常见语法）
# --------------------------------------------------------------------------
_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITAL_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_CODE_INLINE_RE = re.compile(r"`([^`]+)`")
_BARE_URL_RE = re.compile(r'(?<!["\'=>])(https?://[^\s<)\]]+)')


def render_inline(s):
    stash = []

    def _stash(m):
        stash.append(m.group(1))
        return "\x00{}\x00".format(len(stash) - 1)

    s = _CODE_INLINE_RE.sub(_stash, s or "")
    s = esc(s)
    s = _IMG_RE.sub(
        lambda m: '<span class="img-chip">🖼 {}<i>（图不出现在解析版，请回原文核对）</i></span>'
        .format(esc(m.group(1) or "原文插图")), s)
    s = _LINK_RE.sub(
        lambda m: '<a href="{}"{}>{}</a>'.format(
            esc(m.group(2)),
            ' target="_blank" rel="noopener"'
            if m.group(2).startswith("http") else "",
            m.group(1)), s)
    s = _BARE_URL_RE.sub(
        lambda m: '<a href="{}" target="_blank" rel="noopener">{}</a>'
        .format(m.group(1), m.group(1)), s)
    s = _BOLD_RE.sub(r"<strong>\1</strong>", s)
    s = _ITAL_RE.sub(r"<em>\1</em>", s)
    for i, c in enumerate(stash):
        s = s.replace("\x00{}\x00".format(i), "<code>{}</code>".format(esc(c)))
    return s


def render_md(md, slugs, toc=None, prefix=""):
    """
    渲染一段 Markdown。toc 为 (level, text, slug) 列表（收集 h2/h3）。
    支持：标题、段落、围栏代码块、引用块、无序/有序列表（一级嵌套）、表格、
    水平线；自动剔除 <!-- --> 注释；图片以占位 chip 呈现。
    """
    lines = (md or "").replace("\r\n", "\n").split("\n")
    out, i = [], 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()

        if line.startswith("```"):
            buf, i = [], i + 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1  # 跳过收尾 ```
            out.append("<pre><code>{}</code></pre>".format(esc("\n".join(buf))))
            continue

        if not line or line.startswith("<!--"):
            i += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level, text = len(m.group(1)), m.group(2).strip()
            slug = slugs.make(text, prefix)
            if toc is not None and level <= 3:
                toc.append((level, re.sub(r"[#*`]", "", text), slug))
            out.append('<h{0} id="{1}">{2}</h{0}>'.format(
                level, slug, render_inline(text)))
            i += 1
            continue

        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", line):
            out.append("<hr>")
            i += 1
            continue

        if line.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            inner = "<br>".join(render_inline(b) for b in buf if b.strip())
            out.append("<blockquote>{}</blockquote>".format(inner))
            continue

        if line.startswith("|") and i + 1 < len(lines) and \
                re.match(r"^\s*\|[\s:|-]+\|?\s*$", lines[i + 1]):
            header = [c.strip() for c in line.strip("|").split("|")]
            i += 2
            body = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                body.append([c.strip() for c in
                             lines[i].strip().strip("|").split("|")])
                i += 1
            th = "".join("<th>{}</th>".format(render_inline(c))
                         for c in header)
            rows = "".join("<tr>{}</tr>".format(
                "".join("<td>{}</td>".format(render_inline(c))
                        for c in r)) for r in body)
            out.append(
                '<div class="tbl"><table><thead><tr>{}</tr></thead>'
                "<tbody>{}</tbody></table></div>".format(th, rows))
            continue

        m = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", raw)
        if m:
            items = []           # (indent, ordered, text)
            ordered = bool(re.match(r"\d", m.group(2)))
            while i < len(lines):
                mm = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", lines[i])
                if not mm:
                    break
                items.append((len(mm.group(1)),
                              bool(re.match(r"\d", mm.group(2))),
                              mm.group(3)))
                i += 1
            html, stack = [], []  # 简化：两级嵌套
            top_tag = "ol" if ordered else "ul"
            html.append("<{}>".format(top_tag))
            cur_nested = None
            for ind, ordr, text in items:
                if ind >= 2:  # 嵌套项
                    if cur_nested is None:
                        cur_nested = []
                else:
                    if cur_nested is not None:
                        tag = "ol" if cur_nested[0][0] else "ul"
                        html.append("<{0}>{1}</{0}>".format(tag, "".join(
                            "<li>{}</li>".format(render_inline(t))
                            for _, t in cur_nested[1])))
                        cur_nested = None
                    html.append("<li>{}</li>".format(render_inline(text)))
                if cur_nested is not None:
                    cur_nested.append((ordr, text))
            if cur_nested is not None:
                tag = "ol" if cur_nested[0][0] else "ul"
                html.append("<{0}>{1}</{0}>".format(tag, "".join(
                    "<li>{}</li>".format(render_inline(t))
                    for _, t in cur_nested[1])))
            html.append("</{}>".format(top_tag))
            out.append("".join(html))
            continue

        buf = []
        while i < len(lines):
            s2 = lines[i].strip()
            if (not s2 or s2.startswith(("#", ">", "```", "|")) or
                    re.match(r"^(\s*)([-*+]|\d+[.)])\s+", lines[i]) or
                    re.match(r"^(-{3,}|\*{3,}|_{3,})$", s2)):
                break
            buf.append(s2)
            i += 1
        out.append("<p>{}</p>".format(render_inline(" ".join(buf))))

    return "\n".join(out)


# --------------------------------------------------------------------------
# .paper-companion 结构扫描
# --------------------------------------------------------------------------
PAPER_MODULES = [
    ("overview", "📖 章节概览", False),
    ("section-guides", "🧭 要点导读", False),
    ("notes", "💡 亮点与批判注记", False),
    ("citations", "🔗 引用脉络", False),
    ("background", "🌍 背景调研", False),
    ("translation", "📝 完整转写", True),   # 默认折叠
]
MODULE_TITLES = dict((k, t) for k, t, _ in PAPER_MODULES)


def parse_index_table(pc_dir):
    """解析 index.md 的论文索引表 → [{title,source,date,brief,importance,path}]"""
    path = os.path.join(pc_dir, "index.md")
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if not s.startswith("|"):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) < 6 or cells[0] in ("标题", "") or \
                    set(cells[0]) <= set("-: "):
                continue
            imp = cells[4] if len(cells) > 4 else ""
            rows.append({
                "title": cells[0],
                "source": cells[1] if len(cells) > 1 else "",
                "date": cells[2] if len(cells) > 2 else "",
                "brief": cells[3] if len(cells) > 3 else "",
                "importance": imp,
                "path": cells[5] if len(cells) > 5 else "",
            })
    return rows


def row_for_paper(rows, dirname):
    key = norm_key(dirname)
    if not key:
        return None
    for r in rows:
        rt = norm_key(r.get("title"))
        if rt and (rt in key or key in rt):
            return r
    return None


def discover_papers(pc_dir):
    root = os.path.join(pc_dir, "papers")
    papers = []
    if not os.path.isdir(root):
        return papers
    for d in sorted(os.listdir(root)):
        pdir = os.path.join(root, d)
        if not os.path.isdir(pdir):
            continue
        mods = {}
        for key, _, _ in PAPER_MODULES:
            f = os.path.join(pdir, key + ".md")
            if os.path.isfile(f):
                mods[key] = f
        if not mods:
            continue
        papers.append({"dir": d, "path": pdir, "modules": mods})
    return papers


def read_md(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def scan_local_files(workspace, pc_dir, out_dir):
    """扫描本地文件：工作区根 PDF（用户原文）+ .paper-companion 全部 PDF + 门户

    门户分两类，均以 index.html 为入口，靠所在目录名区分：
      - <pc>/**/library/<主题>/index.html  → 检索门户（检索结果的浏览层）
      - <pc>/**/portal/index.html          → 研读门户（精读解析页 + 总导航）
    """
    pdfs, portals, seen = [], [], set()

    def add_pdf(path, kind):
        ap = os.path.abspath(path)
        if ap in seen:
            return
        seen.add(ap)
        pdfs.append({"path": ap, "kind": kind,
                     "size": os.path.getsize(ap) if os.path.isfile(ap) else 0})

    if workspace and os.path.isdir(workspace):
        for f in sorted(os.listdir(workspace)):
            if f.lower().endswith(".pdf") and \
                    os.path.isfile(os.path.join(workspace, f)):
                add_pdf(os.path.join(workspace, f), "原文")

    for base, _dirs, files in os.walk(pc_dir):
        for f in sorted(files):
            ap = os.path.join(base, f)
            if f.lower().endswith(".pdf"):
                add_pdf(ap, "研读全文")
            elif f == "index.html":
                seg = os.sep + "library" + os.sep
                seg_p = os.sep + "portal" + os.sep
                if seg in ap:
                    portals.append({"path": ap, "kind": "检索门户"})
                elif seg_p in ap:
                    portals.append({"path": ap, "kind": "研读门户"})

    for p in pdfs + portals:
        p["href"] = html_rel(out_dir, p["path"])
    return pdfs, portals


# --------------------------------------------------------------------------
# 样式与脚本（内联，自包含）
# --------------------------------------------------------------------------
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#f6f7f9;--card:#fff;--ink:#1c2025;--muted:#5b6470;--line:#e3e7ec;
--accent:#0f766e;--accent2:#12b3a8;--soft:#e6f7f6;--gold:#c98a12;--gold-soft:#fdf4e2;
--red:#d8483f;--red-soft:#fdeceb;--radius:14px;
--shadow:0 1px 3px rgba(16,24,40,.06),0 6px 20px rgba(16,24,40,.05)}
html{scroll-behavior:smooth;scroll-padding-top:76px}
body{background:var(--bg);color:var(--ink);font:15.5px/1.75 -apple-system,
BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
-webkit-font-smoothing:antialiased}
/* ---- 顶栏 ---- */
.topbar{position:fixed;top:0;left:0;right:0;height:56px;background:rgba(255,255,255,.92);
backdrop-filter:blur(8px);border-bottom:1px solid var(--line);z-index:60;
display:flex;align-items:center;gap:12px;padding:0 16px}
.topbar .brand{font-weight:650;font-size:15px;color:var(--accent);white-space:nowrap;
overflow:hidden;text-overflow:ellipsis}
.topbar input{flex:1;max-width:420px;margin-left:auto;padding:8px 13px;border:1px solid var(--line);
border-radius:10px;font-size:13.5px;outline:none;background:var(--bg)}
.topbar input:focus{border-color:var(--accent2);box-shadow:0 0 0 3px var(--soft)}
#menuBtn{display:none;border:1px solid var(--line);background:var(--card);border-radius:9px;
width:36px;height:36px;font-size:17px;cursor:pointer}
#searchInfo{font-size:12px;color:var(--muted);min-width:64px;text-align:right}
/* ---- 布局 ---- */
.layout{display:flex;max-width:1280px;margin:0 auto;padding:76px 20px 80px;gap:26px}
aside{width:264px;flex:none;position:sticky;top:76px;align-self:flex-start;
max-height:calc(100vh - 96px);overflow-y:auto;background:var(--card);border:1px solid var(--line);
border-radius:var(--radius);padding:14px 10px}
aside h5{font-size:11.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;
padding:6px 10px 4px}
aside a{display:block;color:var(--muted);text-decoration:none;font-size:13px;
padding:5px 10px;border-radius:8px;line-height:1.45}
aside a:hover{background:var(--soft);color:var(--accent)}
aside a.l2{padding-left:22px;font-size:12.3px}
aside a.on{background:var(--soft);color:var(--accent);font-weight:600}
main{flex:1;min-width:0}
.backdrop{display:none}
/* ---- 头部 ---- */
.hero{background:linear-gradient(135deg,#0f766e,#12b3a8);color:#fff;border-radius:18px;
padding:30px 30px 26px;margin-bottom:24px}
.hero h1{font-size:25px;font-weight:680;line-height:1.4}
.hero .sub{opacity:.94;margin-top:8px;font-size:14px;line-height:1.7}
.hero .chips{margin-top:14px;display:flex;flex-wrap:wrap;gap:8px}
.chip{background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.24);
padding:4px 12px;border-radius:999px;font-size:12.5px}
/* ---- 通用文章排版 ---- */
.section{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
padding:26px 30px;margin-bottom:24px;box-shadow:var(--shadow)}
.section h2{font-size:21px;font-weight:660;margin:2px 0 14px;line-height:1.45}
.section h3{font-size:16.5px;font-weight:640;margin:20px 0 8px;padding-left:10px;
border-left:4px solid var(--accent2);line-height:1.5}
.section h4{font-size:14.5px;font-weight:640;margin:16px 0 6px;color:var(--accent)}
.section p{margin:9px 0}
.section ul,.section ol{margin:9px 0 9px 22px}
.section li{margin:4px 0}
blockquote{background:var(--bg);border-left:4px solid var(--accent2);border-radius:0 10px 10px 0;
padding:10px 14px;margin:10px 0;color:#39414c;font-size:14px}
pre{background:#0e2430;color:#d9f2ee;border-radius:10px;padding:13px 15px;overflow-x:auto;
font:12.8px/1.6 Consolas,Menlo,"Courier New",monospace;margin:10px 0}
code{font:90% Consolas,Menlo,monospace;background:var(--soft);border-radius:5px;
padding:1px 5px;color:var(--accent)}
pre code{background:none;color:inherit;padding:0}
.tbl{overflow-x:auto;margin:10px 0}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th{background:var(--soft);color:var(--accent);text-align:left;font-weight:620}
th,td{border:1px solid var(--line);padding:7px 10px;vertical-align:top}
tr:nth-child(even) td{background:#fbfcfd}
a{color:var(--accent);word-break:break-all}
.img-chip{display:inline-block;background:var(--gold-soft);color:var(--gold);
border:1px dashed var(--gold);border-radius:8px;padding:2px 10px;font-size:12.5px}
.img-chip i{opacity:.75;font-style:normal;margin-left:6px;font-size:11.5px}
hr{border:0;border-top:1px dashed var(--line);margin:16px 0}
/* ---- 徽章 ---- */
.badges{display:flex;flex-wrap:wrap;gap:7px;margin:2px 0 12px}
.bg{font-size:12px;padding:3px 10px;border-radius:999px;background:#f1f3f6;
color:var(--muted);white-space:nowrap}
.bg.deep{background:var(--gold-soft);color:var(--gold);font-weight:650}
.bg.shallow{background:#eef1f4;color:var(--muted)}
.bg.ok{background:var(--soft);color:var(--accent);font-weight:600}
.brief{background:var(--soft);border-radius:10px;padding:10px 14px;font-size:14.3px;
margin-bottom:6px}
.brief b{color:var(--accent)}
.paper-head .who{font-size:12.8px;color:var(--muted);margin-bottom:8px}
/* ---- 折叠块 ---- */
details.fold{border:1px solid var(--line);border-radius:12px;margin-top:14px;overflow:hidden}
details.fold>summary{cursor:pointer;list-style:none;padding:11px 16px;background:var(--bg);
font-weight:620;font-size:14px;color:var(--accent);user-select:none}
details.fold>summary::-webkit-details-marker{display:none}
details.fold>summary:before{content:"▸ ";display:inline-block;transition:.15s}
details.fold[open]>summary:before{transform:rotate(90deg)}
details.fold .fold-body{padding:16px 20px;border-top:1px solid var(--line)}
/* ---- 文件索引 ---- */
.filelist{display:flex;flex-direction:column;gap:8px;margin-top:10px}
.fitem{display:flex;align-items:center;gap:12px;background:var(--bg);border:1px solid var(--line);
border-radius:10px;padding:10px 14px;text-decoration:none;color:var(--ink);transition:.15s}
.fitem:hover{border-color:var(--accent2);transform:translateX(2px)}
.fitem .ico{font-size:17px}
.fitem .nm{flex:1;min-width:0;font-size:13.5px;overflow:hidden;text-overflow:ellipsis;
white-space:nowrap}
.fitem .kind{font-size:11.5px;padding:2px 9px;border-radius:999px;white-space:nowrap}
.fitem .kind.o{background:var(--gold-soft);color:var(--gold)}
.fitem .kind.d{background:var(--soft);color:var(--accent)}
.fitem .sz{font-size:12px;color:var(--muted);white-space:nowrap}
.note{font-size:12.8px;color:var(--muted);background:var(--bg);border-radius:10px;
padding:9px 13px;margin-top:12px;line-height:1.7}
/* ---- 其它 ---- */
.toTop{position:fixed;right:20px;bottom:24px;width:42px;height:42px;border-radius:50%;
border:1px solid var(--line);background:var(--card);color:var(--accent);font-size:17px;
cursor:pointer;box-shadow:var(--shadow);display:none;z-index:55}
footer{text-align:center;color:var(--muted);font-size:12.5px;line-height:1.9;margin-top:26px}
mark{background:var(--gold-soft);padding:0 2px;border-radius:3px}
.empty{color:var(--muted);font-size:13.5px}
/* ---- 响应式 ---- */
@media(max-width:960px){
 #menuBtn{display:block}
 .layout{padding:72px 14px 70px;gap:0}
 aside{position:fixed;left:0;top:56px;bottom:0;width:280px;max-height:none;z-index:70;
 border-radius:0;transform:translateX(-105%);transition:.22s}
 body.nav-open aside{transform:none}
 body.nav-open .backdrop{display:block;position:fixed;inset:56px 0 0;background:rgba(17,24,39,.4);z-index:65}
 .topbar input{max-width:none}
 .hero{padding:22px 18px}.hero h1{font-size:20px}
 .section{padding:20px 16px;border-radius:12px}
 .topbar .brand{max-width:34vw}
}
@media print{
 .topbar,aside,.toTop,.backdrop{display:none!important}
 .layout{padding:0;display:block;max-width:none}
 .section{box-shadow:none;border:none;padding:10px 0;page-break-inside:avoid}
 details.fold{border:none}details.fold .fold-body{border:none}
 body{background:#fff}
}
"""

JS = """
(function(){
var input=document.getElementById('q'),info=document.getElementById('searchInfo');
var blocks=[].slice.call(document.querySelectorAll('.paper-block'));
var toTop=document.getElementById('toTop');
function esc(s){return s.replace(/[.*+?^${}()|[\\]\\\\]/g,'\\\\$&');}
input.addEventListener('input',function(){
  var q=input.value.trim().toLowerCase(),hit=0;
  blocks.forEach(function(b){
    var on=!q||b.textContent.toLowerCase().indexOf(q)>=0;
    b.style.display=on?'':'none';if(on)hit++;});
  if(info)info.textContent=q?(hit+'/'+blocks.length+' 篇'):'';
});
input.addEventListener('keydown',function(e){
  if(e.key==='Escape'){input.value='';input.dispatchEvent(new Event('input'));}
});
/* 抽屉 */
var mb=document.getElementById('menuBtn'),bd=document.getElementById('backdrop');
mb.addEventListener('click',function(){document.body.classList.toggle('nav-open');});
bd.addEventListener('click',function(){document.body.classList.remove('nav-open');});
[].slice.call(document.querySelectorAll('aside a')).forEach(function(a){
  a.addEventListener('click',function(){document.body.classList.remove('nav-open');});});
/* 滚动高亮 + 返回顶部 */
var links={};[].slice.call(document.querySelectorAll('aside a')).forEach(function(a){
  var id=(a.getAttribute('href')||'').slice(1);if(id)links[id]=a;});
var spy=new IntersectionObserver(function(es){
  es.forEach(function(en){
    if(en.isIntersecting){
      [].slice.call(document.querySelectorAll('aside a.on'))
        .forEach(function(x){x.classList.remove('on');});
      var a=links[en.target.id];if(a)a.classList.add('on');}});
},{rootMargin:'-70px 0px -70% 0px'});
[].slice.call(document.querySelectorAll('[data-spy]')).forEach(function(s){spy.observe(s);});
window.addEventListener('scroll',function(){
  toTop.style.display=window.scrollY>600?'block':'none';},{passive:true});
toTop.addEventListener('click',function(){window.scrollTo({top:0,behavior:'smooth'});});
/* 打印时展开全部折叠块 */
window.addEventListener('beforeprint',function(){
  [].slice.call(document.querySelectorAll('details.fold')).forEach(function(d){d.open=true;});});
})();
"""

HTML_TMPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>__CSS__</style>
</head>
<body>
<nav class="topbar">
  <button id="menuBtn" aria-label="目录">☰</button>
  <div class="brand">__BRAND__</div>
  <input type="search" id="q" placeholder="全文搜索：标题 / 摘要 / 术语…">
  <span id="searchInfo"></span>
</nav>
<div class="backdrop" id="backdrop"></div>
<div class="layout">
  <aside id="side">__NAV__</aside>
  <main>
    <header class="hero" data-spy id="sec-top">
      <h1>__TITLE__</h1>
      <div class="sub">__SUB__</div>
      <div class="chips">__HEROCHIPS__</div>
    </header>
    __CONTENT__
    <footer>__FOOT__</footer>
  </main>
</div>
<button class="toTop" id="toTop" aria-label="返回顶部">↑</button>
<script>__JS__</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# 组装
# --------------------------------------------------------------------------
def build(workspace, title=None, out=None, deploy_url=None):
    pc_dir = os.path.join(workspace, ".paper-companion") if workspace else ""
    if not pc_dir or not os.path.isdir(pc_dir):
        print("错误：在 {} 下找不到 .paper-companion/ 目录".format(workspace))
        sys.exit(2)
    out = out or os.path.join(pc_dir, "digest.html")
    out_dir = os.path.dirname(os.path.abspath(out))
    os.makedirs(out_dir, exist_ok=True)

    slugs = SlugPool()
    title = title or (os.path.basename(os.path.normpath(workspace)) +
                      " · 论文研读解析版")
    papers = discover_papers(pc_dir)
    index_rows = parse_index_table(pc_dir)
    pdfs, portals = scan_local_files(workspace, pc_dir, out_dir)

    toc_main = [("top", "页首", "sec-top")]
    content = []

    # 1) 总导读（可选 digest-intro.md）
    intro_path = os.path.join(pc_dir, "digest-intro.md")
    if os.path.isfile(intro_path):
        toc_main.append(("intro", "总导读", "sec-intro"))
        intro_html = render_md(read_md(intro_path), slugs, prefix="intro-")
        content.append(
            '<section class="section" id="sec-intro" data-spy>'
            "<h2>🧭 总导读</h2>{}</section>".format(intro_html))

    # 2) 总索引
    index_md = read_md(os.path.join(pc_dir, "index.md"))
    n_md = 0
    for base, _d, files in os.walk(pc_dir):
        n_md += sum(1 for f in files if f.endswith(".md"))
    if index_md.strip():
        toc_main.append(("index", "总索引", "sec-index"))
        idx_html = render_md(index_md, slugs, prefix="idx-")
        content.append(
            '<section class="section" id="sec-index" data-spy>'
            "<h2>🗂 研读总索引</h2>{}</section>".format(idx_html))

    # 3) 每篇论文一个区块
    for pi, p in enumerate(papers, 1):
        row = row_for_paper(index_rows, p["dir"]) or {}
        is_deep = "深" in (row.get("importance") or "")
        title_p = row.get("title") or p["dir"]
        anchor = slugs.make(title_p, "paper-")
        toc_main.append((1, title_p, anchor))

        badges = ['<span class="bg {}">{}</span>'.format(
            "deep" if is_deep else "shallow",
            "重要度 · 深" if is_deep else "重要度 · 浅")]
        matched_pdf = None
        nk = norm_key(title_p) or norm_key(p["dir"])
        for f in pdfs:
            fk = norm_key(os.path.splitext(os.path.basename(f["path"]))[0])
            if nk and fk and (nk in fk or fk in nk):
                matched_pdf = f
                break
        if matched_pdf:
            badges.append('<span class="bg ok">📄 本地全文</span>')

        who = " · ".join([x for x in (row.get("source"), row.get("date"))
                          if x])
        head = ("" if not who else
                '<div class="who">{}</div>'.format(esc(who)))

        body_parts = []
        if row.get("brief"):
            body_parts.append(
                '<div class="brief"><b>一句话核心：</b>{}</div>'.format(
                    render_inline(row["brief"])))
        if matched_pdf:
            body_parts.append(
                '<p>📄 <a href="{}" target="_blank" rel="noopener">'
                '打开本地全文 PDF（{}）</a></p>'.format(
                    matched_pdf["href"], human_size(matched_pdf["size"])))

        for key, label, fold in PAPER_MODULES:
            f = p["modules"].get(key)
            if not f:
                continue
            sub_toc = []
            html_m = render_md(read_md(f), slugs, toc=sub_toc,
                               prefix=anchor + "-")
            for lv, t, sl in sub_toc:
                if lv <= 3:
                    toc_main.append((2, t, sl))
            if fold:
                n_chars = len(html_m)
                body_parts.append(
                    '<details class="fold"><summary>{}（约 {} 字 · 点开阅读）'
                    '</summary><div class="fold-body">{}</div></details>'
                    .format(label, n_chars, html_m))
            else:
                body_parts.append(
                    '<h3>{}</h3>{}'.format(label, html_m))

        content.append(
            '<section class="section paper-block" id="{a}" data-spy>'
            '<div class="paper-head"><h2>{t}</h2>{head}'
            '<div class="badges">{b}</div></div>{body}</section>'.format(
                a=anchor, t=esc(title_p), head=head,
                b="".join(badges), body="".join(body_parts)))

    if not papers:
        content.append(
            '<section class="section"><h2>🗂 论文区块</h2>'
            '<p class="empty">papers/ 下暂无已沉淀的论文笔记——'
            '先让论文研读助手精读几篇，再重新生成本解析版。</p></section>')

    # 4) 本地文件索引（本工具的核心差异化能力）
    toc_main.append(("files", "本地文件索引", "sec-files"))
    items = []
    for f in pdfs:
        kind_cls = "o" if f["kind"] == "原文" else "d"
        items.append(
            '<a class="fitem" href="{h}" target="_blank" rel="noopener">'
            '<span class="ico">📄</span><span class="nm">{n}</span>'
            '<span class="kind {k}">{kind}</span>'
            '<span class="sz">{sz}</span></a>'.format(
                h=f["href"], n=esc(os.path.basename(f["path"])),
                k=kind_cls, kind=esc(f["kind"]), sz=human_size(f["size"])))
    for pt in portals:
        items.append(
            '<a class="fitem" href="{h}" target="_blank" rel="noopener">'
            '<span class="ico">🧩</span><span class="nm">{n}</span>'
            '<span class="kind d">{k}</span><span class="sz">HTML</span>'
            "</a>".format(h=pt["href"],
                          k=esc(pt.get("kind", "检索门户")),
                          n=esc(os.path.relpath(pt["path"], pc_dir)
                                .replace(os.sep, "/"))))
    if not items:
        items.append('<p class="empty">当前工作区暂无本地 PDF / 门户文件。</p>')
    n_root = sum(1 for f in pdfs if f["kind"] == "原文")
    n_pc = len(pdfs) - n_root
    content.append(
        '<section class="section" id="sec-files" data-spy>'
        "<h2>🗃 本地文件索引</h2>"
        "<p>解析版直接索引本地文件：点开即可阅读。共 {n} 个 PDF"
        "（原文 {a} · 全文 {b}）与 {p} 个门户页面（检索门户 / 研读门户）。</p>"
        '<div class="filelist">{items}</div>'
        '<div class="note">💡 相对路径链接在<b>本地打开</b>或<b>整个工作区目录'
        '一起部署</b>时有效；仅单独上传本 HTML 时，请改用各论文的 arXiv/DOI '
        "原文链接（见每篇开头）。文件未做任何移动或改名。</div></section>"
        .format(n=len(pdfs), a=n_root, b=n_pc, p=len(portals),
                items="".join(items)))

    # 5) 侧边导航
    nav = ["<h5>目录</h5>"]
    for lv, text, slug in toc_main:
        lvl = lv if isinstance(lv, int) else 1
        nav.append('<a class="{}" href="#{}" data-spy-link>{}</a>'.format(
            "l2" if lvl >= 2 else "", slug, esc(text)))
    nav_html = "".join(nav)

    total_chars = 0
    for base, _d, files in os.walk(pc_dir):
        for f in files:
            if f.endswith(".md"):
                try:
                    total_chars += os.path.getsize(os.path.join(base, f))
                except OSError:
                    pass

    chips = [
        '<span class="chip">🗓 {}</span>'.format(
            time.strftime("%Y-%m-%d %H:%M")),
        '<span class="chip">📚 {} 篇论文</span>'.format(len(papers)),
        '<span class="chip">📝 约 {:.1f} 万字笔记</span>'.format(
            total_chars / 10000.0),
        '<span class="chip">📄 {} 个本地 PDF 已索引</span>'.format(
            len(pdfs)),
    ]
    sub = ("单文件自包含解析版：目录导航 + 全文搜索 + 本地文件索引。"
           "工作流内部仍以 Markdown 沉淀，本页为其合并视图。")

    foot = ("由 <b>论文研读助手 (paper-companion)</b> · build_digest 生成 · "
            "内容全部来自工作区真实研读笔记，未作虚构")
    if deploy_url:
        foot += " · 在线地址：<a href='{0}'>{0}</a>".format(
            esc(deploy_url))

    html = (HTML_TMPL
            .replace("__TITLE__", esc(title))
            .replace("__BRAND__", esc(title.split("·")[0].strip()))
            .replace("__CSS__", CSS)
            .replace("__JS__", JS)
            .replace("__NAV__", nav_html)
            .replace("__SUB__", esc(sub))
            .replace("__HEROCHIPS__", "".join(chips))
            .replace("__CONTENT__", "\n".join(content))
            .replace("__FOOT__", foot))

    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return out, len(papers), len(pdfs), len(portals)


def main():
    ap = argparse.ArgumentParser(description="paper-digest 解析版全集生成器")
    ap.add_argument("--workspace", required=True,
                    help="包含 .paper-companion/ 的工作区路径")
    ap.add_argument("--title", default=None)
    ap.add_argument("--out", default=None,
                    help="输出路径（默认 <workspace>/.paper-companion/digest.html）")
    ap.add_argument("--deploy-url", default=None,
                    help="部署后的公网地址（写入页脚）")
    args = ap.parse_args()

    out, n_papers, n_pdfs, n_portals = build(
        args.workspace, args.title, args.out, args.deploy_url)
    size = os.path.getsize(out)
    print("解析版已生成：{}".format(out))
    print("大小：{:.1f} KB　论文区块：{}　本地 PDF 索引：{}　门户：{}".format(
        size / 1024, n_papers, n_pdfs, n_portals))


if __name__ == "__main__":
    main()
