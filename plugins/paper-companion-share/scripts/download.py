#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
paper-library · PDF 批量下载器

输入：search.py --json 产出的结构化结果
输出：把可开放获取的 PDF 下载到 <out-dir>/pdfs/，并生成带本地路径的 library.json

候选源优先级（实测结论）：
  1. arXiv 直链      —— 预印本最可靠；注意 Unpaywall 对 arXiv DOI(10.48550/…) 返回 404，
                        所以 arXiv 一律走 https://arxiv.org/pdf/<id>.pdf
  2. OpenAlex pdf_url —— best_oa_location.pdf_url，期刊 OA 论文常用
  3. Unpaywall       —— 仅对「非 arXiv 的期刊 DOI」尝试，补 OA 覆盖
  4. landing_url      —— 仅当以 .pdf 结尾

校验：下载后必须校验魔数 %PDF，且体积 > 8KB，避免把网站的 HTML 错误页存成假 PDF。
"""

import os
import sys
import json
import time
import argparse
import urllib.request
import urllib.parse
import re
from concurrent.futures import ThreadPoolExecutor

DEFAULT_PROXY = os.environ.get("PAPER_COMPANION_PROXY", "")
UA = "Mozilla/5.0 (compatible; paper-library/1.0; +academic research)"
MAILTO = "paper-explorer@local"


def build_opener(proxy):
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener()


def slugify(text, maxlen=60):
    """生成安全文件名：保留中英文数字，其余转下划线"""
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "_", (text or "").strip())
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "paper"
    return s[:maxlen].rstrip("_")


def pdf_candidates(rec):
    """按优先级列出该论文所有可能的 PDF 直链"""
    cands, arxiv_id = [], (rec.get("arxiv_id") or "").strip()
    if arxiv_id:
        aid = arxiv_id.rsplit("/", 1)[-1]
        aid = re.sub(r"v\d+$", "", aid)          # 去掉版本号更稳
        cands.append("https://arxiv.org/pdf/{}.pdf".format(aid))
        cands.append("https://arxiv.org/pdf/{}v1.pdf".format(aid))

    acc = rec.get("access") or {}
    pu = (acc.get("pdf_url") or "").strip()
    if pu.lower().endswith(".pdf") or "pdf" in pu.lower():
        cands.append(pu)

    doi = (rec.get("doi") or "").strip()
    lu = (acc.get("landing_url") or "").strip()
    if lu.lower().endswith(".pdf"):
        cands.append(lu)
    if not arxiv_id and doi:
        cands.append("__unpaywall__")            # 占位，运行时再解析

    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def unpaywall_pdf(opener, doi):
    """Unpaywall 查 OA 全文（仅对期刊 DOI 有效）"""
    try:
        url = "https://api.unpaywall.org/v2/{}?email={}".format(
            urllib.parse.quote(doi, safe=""), MAILTO)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with opener.open(req, timeout=30) as r:
            j = json.loads(r.read().decode("utf-8", "ignore"))
        for loc in [j.get("best_oa_location")] + (j.get("oa_locations") or []):
            if not loc:
                continue
            u = loc.get("url_for_pdf") or loc.get("pdf_url")
            if u:
                return u
    except Exception:
        pass
    return None


def is_pdf(blob):
    return blob[:4] == b"%PDF" and len(blob) > 8 * 1024


def try_download(opener, rec, out_dir, idx):
    """逐个候选源尝试，成功返回相对路径，失败返回 (None, 原因)"""
    title = rec.get("title") or "paper"
    fname = "{:02d}_{}_{}.pdf".format(
        idx, rec.get("year") or "nd", slugify(title))
    path = os.path.join(out_dir, "pdfs", fname)

    for cand in pdf_candidates(rec):
        url = cand
        if cand == "__unpaywall__":
            url = unpaywall_pdf(opener, rec.get("doi"))
            if not url:
                continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with opener.open(req, timeout=90) as r:
                blob = r.read()
            if is_pdf(blob):
                with open(path, "wb") as f:
                    f.write(blob)
                return "pdfs/" + fname, None
        except Exception:
            continue
        time.sleep(0.3)
    return None, "无开放获取全文（需机构权限或付费）"


def main():
    ap = argparse.ArgumentParser(description="paper-library PDF 批量下载")
    ap.add_argument("--input", required=True, help="search.py --json-out 的 JSON")
    ap.add_argument("--out-dir", required=True, help="输出目录（含 pdfs/ 子目录）")
    ap.add_argument("--max", type=int, default=0, help="最多下载几篇（0=全部）")
    ap.add_argument("--proxy", default=DEFAULT_PROXY)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)
    results = data.get("results", [])
    if args.max:
        results = results[:args.max]

    os.makedirs(os.path.join(args.out_dir, "pdfs"), exist_ok=True)
    opener = build_opener(args.proxy)

    jobs = [(i, r) for i, r in enumerate(results, 1)]

    def work(job):
        i, r = job
        rel, err = try_download(opener, r, args.out_dir, i)
        return i, r, rel, err

    ok = fail = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, r, rel, err in ex.map(work, jobs):
            if rel:
                r["local_pdf"] = rel
                ok += 1
                print("  ✅ [{:>2}] {}".format(i, (r.get("title") or "")[:60]))
            else:
                r["local_pdf"] = None
                r["download_error"] = err
                fail += 1
                print("  ⬜ [{:>2}] {} —— {}".format(
                    i, (r.get("title") or "")[:52], err))

    data["results"] = results
    data["downloaded"] = ok
    data["download_failed"] = fail
    out_json = os.path.join(args.out_dir, "library.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print()
    print("下载完成：成功 {} 篇 / 无全文 {} 篇".format(ok, fail))
    print("PDF 目录：{}".format(os.path.join(args.out_dir, "pdfs")))
    print("数据文件：{}".format(out_json))


if __name__ == "__main__":
    main()
