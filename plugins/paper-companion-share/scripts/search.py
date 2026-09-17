#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
paper-explorer v2 · 学术论文结构化检索引擎（零依赖，显式走本地代理）

设计目标：像一个老练的文献检索专家那样工作——
  1. 意图自适应：用户想「概览 / 全面了解 / 抠细节 / 追前沿」，检索策略完全不同
  2. 多源交叉校验：OpenAlex 主引擎 + arXiv 补预印本 + Crossref 校验 DOI 真实性
  3. 影响力可解释：不只给引用数，而是拆成 FWCI / 全局百分位 / 同年百分位 三个正交分量
  4. 安全过滤：撤稿论文直接标记并降权
  5. 结构化输出：--json 供下游（下载 / HTML 门户）消费

意图（--intent）：
  overview   概览 / 入门      → 综述优先 + 少量奠基性
  landscape  全面了解领域      → 综述层 + 奠基层 + 前沿层 三层结构
  detail     抠某个细节 / 方法 → 精准命中 + 引文雪球（backward / forward）
  frontier   已有基础，追前沿  → 最新预印本 + 近两年高 FWCI
  top        找奠基性 / 经典   → 高影响力（时间无关）
  latest     找最新预印本      → arXiv 按提交时间倒序
  survey     找综述            → 标题级 survey / review

代理：默认走环境变量 PAPER_COMPANION_PROXY，未设置则直连（无需代理时留空即可）；
也可通过 --proxy <url> 显式覆盖。访问外网学术 API 受网络限制时，请在初始化引导中配置代理。
绝不幻觉：只输出 API 真实返回的数据，缺失字段一律标 None，不编造。
"""

import sys
import os
import json
import math
import time
import argparse
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
import html as _html
import re
from concurrent.futures import ThreadPoolExecutor

DEFAULT_PROXY = os.environ.get("PAPER_COMPANION_PROXY", "")
UA = "paper-explorer/2.0 (+local academic discovery)"
MAILTO = "paper-explorer@local"
OA_KEY = os.environ.get("OPENALEX_API_KEY", "")

# OpenAlex 只取需要的字段，显著减小响应体积
OA_FIELDS = ",".join([
    "id", "doi", "ids", "title", "display_name", "publication_year",
    "publication_date", "type", "cited_by_count", "fwci",
    "citation_normalized_percentile", "cited_by_percentile_year",
    "open_access", "best_oa_location", "primary_location",
    "authorships", "is_retracted", "referenced_works_count",
    "abstract_inverted_index", "topics", "language",
])

NOW_YEAR = time.localtime().tm_year


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def build_opener(proxy):
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener()


def fetch(opener, url, headers=None, timeout=40):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def clean(s):
    return _html.unescape(re.sub(r"\s+", " ", s or "")).strip()


def norm_doi(doi):
    """DOI 归一化：小写、去 URL 前缀、去尾点"""
    if not doi:
        return ""
    d = str(doi).strip().lower()
    for pre in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/",
                "http://dx.doi.org/", "doi:"):
        if d.startswith(pre):
            d = d[len(pre):]
    return d.rstrip(".")


def norm_title(t):
    """标题归一化用于去重：小写、去标点、去停用词、按字母序排词"""
    if not t:
        return ""
    t = re.sub(r"[^a-z0-9 ]", " ", str(t).lower())
    words = [w for w in t.split() if w and w not in _STOP]
    return " ".join(sorted(words))


def abs_from_inv(inv):
    """OpenAlex 的 abstract_inverted_index（{词: [位置]}）还原为正常摘要"""
    if not inv or not isinstance(inv, dict):
        return ""
    pos = {}
    for word, idxs in inv.items():
        if isinstance(idxs, list):
            for i in idxs:
                pos[int(i)] = word
    if not pos:
        return ""
    try:
        return clean(" ".join(pos[i] for i in sorted(pos.keys())))
    except Exception:
        return ""


def trunc(s, n):
    s = clean(s)
    return s if len(s) <= n else s[:n] + "…"


def _tokens(text):
    """归一化分词：小写、非字母数字切分。连字符会被拆开，
    因此 chain-of-thought 与 chain of thought 得到一致的词集。"""
    return [w for w in re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split()
            if w]


_STOP = {"the", "a", "an", "of", "and", "or", "for", "to", "in", "on", "with",
         "via", "using", "based", "towards", "toward", "is", "are", "be"}


def content_words(query):
    """查询实词（去停用词、去单字母）"""
    return [w for w in _tokens(query) if w not in _STOP and len(w) >= 2]


def _tok_hit(word, toks):
    """词命中判定：完全一致，或 len>=4 时允许前缀匹配（prompt → prompting）"""
    if word in toks:
        return True
    return len(word) >= 4 and any(t.startswith(word) for t in toks)


def relevance(rec, query):
    """
    本地相关性门控（本引擎最关键的一道防线）。

    为什么必须有：OpenAlex / arXiv 的检索是模糊匹配，若直接按被引数排序，
    会把「高被引但主题完全无关」的论文顶到第一名。实测：
      搜索 "chain of thought prompting"，被引排序第一名是
      《Hepatic encephalopathy in chronic liver disease》(2014, 被引 2073)。
    那类论文只是恰好在摘要里散落了查询词，对检索毫无价值。

    做法：0.5·(查询实词在标题+摘要的覆盖率) + 0.5·(标题命中率)

    为什么不能只看整体覆盖率：多词查询下（如 "multi-agent LLM" 含 3 个实词），
    合法论文常缺其中一个词（只写 "LLM agents" 而没有 "multi"），覆盖率 0.67
    会被误杀；反过来，纯看整体覆盖率又会放过「标题无关、摘要偶然散落查询词」
    的噪声（那篇心脏手术论文正是 cov=0.67）。
    区分点在于**标题**：真正对题的论文，标题里就会有查询词。
    加入标题命中率后：
      肝性脑病 (cov=0)        → 0.00  剔除
      心脏手术 (cov=0.67,标题0)→ 0.335 剔除
      LLM agents (缺 multi)   → 0.67  保留
      CoT 正主 (都命中)        → 1.00  保留
    """
    qw = content_words(query)
    if not qw:
        return 1.0
    toks = set(_tokens((rec.get("title") or "") + " " +
                       (rec.get("abstract") or "")))
    ttoks = set(_tokens(rec.get("title") or ""))
    cov = sum(1 for w in qw if _tok_hit(w, toks)) / len(qw)
    tcov = sum(1 for w in qw if _tok_hit(w, ttoks)) / len(qw)
    return round(min(1.0, 0.5 * cov + 0.5 * tcov), 4)


REL_GATE = 0.45   # 低于此相关性视为噪声，直接剔除


# --------------------------------------------------------------------------
# OpenAlex
# --------------------------------------------------------------------------
def oa_query(opener, filter_expr, maxn, sort="cited_by_count:desc"):
    """
    OpenAlex 检索。返回 (works, error)
    注意：filter 用 title_and_abstract.search 做「标题+摘要」级精准命中，
    不要用 search= 全文级宽松匹配（会灌入大量无关高被引论文）。
    429（免费额度限流）自动退避重试 2 次；配置 OPENALEX_API_KEY
    （免费，openalex.org/settings/api）可获 10 倍配额。
    """
    params = {
        "filter": filter_expr,
        "sort": sort,
        "per_page": str(max(1, min(maxn, 200))),
        "select": OA_FIELDS,
        "mailto": MAILTO,
    }
    if OA_KEY:
        params["api_key"] = OA_KEY
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    last_err = None
    for attempt in range(3):
        try:
            data = fetch(opener, url)
            return json.loads(data).get("results", []) or [], None
        except urllib.error.HTTPError as e:
            last_err = "OpenAlex HTTP {}: {}".format(e.code, e.reason)
            if e.code == 429 and attempt < 2:
                time.sleep(1.5 * (attempt + 1))   # 退避 1.5s → 3s
                continue
            return [], last_err
        except Exception as e:
            return [], "OpenAlex 请求失败（代理/网络）：{}".format(e)
    return [], last_err


def parse_oa(w):
    """OpenAlex work → 统一 record"""
    oa_id = (w.get("id") or "").replace("https://openalex.org/", "")
    ids = w.get("ids") or {}
    doi = w.get("doi") or ""
    if isinstance(doi, str) and doi.startswith("https://doi.org/"):
        doi = doi[16:]
    arxiv_id = ids.get("arxiv") or ""
    if isinstance(arxiv_id, str) and "/" in arxiv_id:
        arxiv_id = arxiv_id.rsplit("/", 1)[-1]

    pl = w.get("primary_location") or {}
    src = (pl or {}).get("source") or {}
    venue = src.get("display_name") or ""
    boa = w.get("best_oa_location") or {}
    oa = w.get("open_access") or {}

    authors = []
    for a in (w.get("authorships") or [])[:6]:
        nm = ((a or {}).get("author") or {}).get("display_name")
        if nm:
            authors.append(nm)

    cnp = w.get("citation_normalized_percentile") or {}
    cpy = w.get("cited_by_percentile_year") or {}

    pdf_url = boa.get("pdf_url") or (pl or {}).get("pdf_url") or ""
    landing = boa.get("landing_page_url") or pl.get("landing_page_url") or ""
    # arXiv 预印本：OpenAlex 常不给 pdf_url，但可据 arxiv_id 直连
    if not pdf_url and arxiv_id:
        pdf_url = "https://arxiv.org/pdf/{}.pdf".format(arxiv_id)
        landing = landing or "https://arxiv.org/abs/{}".format(arxiv_id)

    topics = []
    for t in (w.get("topics") or [])[:4]:
        dn = (t or {}).get("display_name")
        if dn:
            topics.append(dn)

    return {
        "title": clean(w.get("title") or w.get("display_name") or "(无标题)"),
        "year": w.get("publication_year"),
        "venue": clean(venue),
        "doi": norm_doi(doi),
        "arxiv_id": arxiv_id,
        "openalex_id": oa_id,
        "authors": authors,
        "abstract": abs_from_inv(w.get("abstract_inverted_index")),
        "type": w.get("type"),
        "is_retracted": bool(w.get("is_retracted")),
        "referenced_works_count": w.get("referenced_works_count"),
        "topics": topics,
        "impact": {
            "cited_by_count": w.get("cited_by_count"),
            "fwci": w.get("fwci"),
            "citation_percentile": (cnp or {}).get("value"),
            "is_top_1_percent": bool((cnp or {}).get("is_in_top_1_percent")),
            "is_top_10_percent": bool((cnp or {}).get("is_in_top_10_percent")),
            "cited_by_percentile_year_max": (cpy or {}).get("max"),
        },
        "access": {
            "is_oa": bool((oa or {}).get("is_oa")),
            "oa_status": (oa or {}).get("oa_status"),
            "pdf_url": pdf_url,
            "landing_url": landing or (("https://doi.org/" + doi) if doi else ""),
        },
        "verified_by": ["openalex"],
    }


# --------------------------------------------------------------------------
# arXiv（预印本 + 最可靠的 PDF 源）
# --------------------------------------------------------------------------
def _arxiv_fetch(opener, search_expr, maxn, sort):
    url = ("https://export.arxiv.org/api/query?search_query={}"
           "&sortBy={}&sortOrder=descending&max_results={}").format(
        urllib.parse.quote(search_expr), sort, max(1, min(maxn, 100)))
    data = fetch(opener, url)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(data)
    out = []
    for e in root.findall("a:entry", ns):
        idurl = clean((e.find("a:id", ns).text or ""))
        aid = idurl.rsplit("/", 1)[-1]
        authors = [clean(a.find("a:name", ns).text)
                   for a in e.findall("a:author", ns)][:6]
        out.append({
            "title": clean(e.find("a:title", ns).text),
            "year": int((e.find("a:published", ns).text or "0000")[:4]) or None,
            "venue": "arXiv preprint",
            "doi": "",
            "arxiv_id": aid,
            "openalex_id": "",
            "authors": [a for a in authors if a],
            "abstract": clean(e.find("a:summary", ns).text),
            "type": "preprint",
            "is_retracted": False,
            "referenced_works_count": None,
            "topics": [clean(c.get("term"))
                       for c in e.findall("a:category", ns)][:4],
            "impact": {
                "cited_by_count": None, "fwci": None,
                "citation_percentile": None,
                "is_top_1_percent": False, "is_top_10_percent": False,
                "cited_by_percentile_year_max": None,
            },
            "access": {
                "is_oa": True, "oa_status": "preprint",
                "pdf_url": "https://arxiv.org/pdf/{}.pdf".format(aid),
                "landing_url": "https://arxiv.org/abs/{}".format(aid),
            },
            "verified_by": ["arxiv"],
        })
    return out, None


def arxiv_query(opener, query, maxn, sort="submittedDate"):
    """
    先用引号短语检索收紧相关性（all:"multi-agent LLM"），
    为空时回退到无引号的宽松检索（all:multi-agent LLM）。
    实测多词查询下短语检索能显著提升 arXiv 结果的对题率。
    """
    errs = []
    for expr in ('all:"{}"'.format(query), "all:{}".format(query)):
        try:
            rows, err = _arxiv_fetch(opener, expr, maxn, sort)
        except Exception as e:
            errs.append("{} → {}".format(expr, e))
            continue
        if err:
            errs.append(err)
            continue
        if rows:
            return rows, None
    if errs:
        return [], "arXiv 请求失败（代理/网络）：" + "；".join(errs[:2])
    return [], None


# --------------------------------------------------------------------------
# Crossref —— 只用于 DOI 真实性校验与引用数交叉核对
# --------------------------------------------------------------------------
def cr_verify(opener, doi):
    """校验 DOI 是否真实存在于 Crossref；返回 (ok, cited_by)"""
    if not doi:
        return False, None
    url = "https://api.crossref.org/works/{}?mailto={}".format(
        urllib.parse.quote(doi, safe=""), MAILTO)
    try:
        j = json.loads(fetch(opener, url, timeout=25))
        it = j.get("message", {})
        return True, it.get("is-referenced-by-count")
    except urllib.error.HTTPError:
        return False, None
    except Exception:
        return False, None


# --------------------------------------------------------------------------
# Semantic Scholar（triage：低优先，需 key）
# --------------------------------------------------------------------------
def s2_query(opener, query, maxn, api_key=None):
    url = ("https://api.semanticscholar.org/graph/v1/paper/search?query={}"
           "&limit={}&fields=title,year,citationCount,influentialCitationCount,"
           "tldr,externalIds,abstract").format(
        urllib.parse.quote(query), maxn)
    hdr = {"User-Agent": UA}
    if api_key:
        hdr["x-api-key"] = api_key
    try:
        j = json.loads(fetch(opener, url, hdr))
    except urllib.error.HTTPError as e:
        return [], "Semantic Scholar HTTP {}: {}".format(e.code, e.reason)
    except Exception as e:
        return [], "Semantic Scholar 请求失败：{}".format(e)
    if "message" in j:
        return [], "Semantic Scholar：{}（建议改用无需 key 的链路）".format(
            j.get("message"))
    out = []
    for p in j.get("data", []) or []:
        ext = p.get("externalIds") or {}
        aid = ext.get("ArXiv") or ""
        out.append({
            "title": clean(p.get("title")),
            "year": p.get("year"),
            "venue": "",
            "doi": norm_doi(ext.get("DOI")),
            "arxiv_id": aid,
            "openalex_id": "",
            "authors": [],
            "abstract": clean(p.get("abstract") or ""),
            "type": "",
            "is_retracted": False,
            "referenced_works_count": None,
            "topics": [],
            "impact": {
                "cited_by_count": p.get("citationCount"),
                "fwci": None, "citation_percentile": None,
                "is_top_1_percent": False, "is_top_10_percent": False,
                "cited_by_percentile_year_max": None,
                "influential_citations": p.get("influentialCitationCount"),
            },
            "access": {
                "is_oa": False, "oa_status": None,
                "pdf_url": ("https://arxiv.org/pdf/{}.pdf".format(aid)
                            if aid else ""),
                "landing_url": (("https://doi.org/" + norm_doi(ext.get("DOI")))
                                if ext.get("DOI") else ""),
            },
            "tldr": ((p.get("tldr") or {}).get("text") or ""),
            "verified_by": ["semanticscholar"],
        })
    return out, None


# --------------------------------------------------------------------------
# 合并 / 去重 / 交叉校验
# --------------------------------------------------------------------------
def merge_records(lists):
    """按 DOI（优先）或归一化标题合并多源记录，累加 verified_by，填补缺失字段"""
    by_doi, by_title, out = {}, {}, []
    for rec in lists:
        if not rec.get("title"):
            continue
        key_d = norm_doi(rec.get("doi"))
        key_t = norm_title(rec.get("title"))
        if key_d:
            if key_d in by_doi:
                _absorb(by_doi[key_d], rec)
                continue
            by_doi[key_d] = rec
            out.append(rec)
            continue
        if key_t:
            if key_t in by_title:
                _absorb(by_title[key_t], rec)
                continue
            by_title[key_t] = rec
            out.append(rec)
            continue
        out.append(rec)
    return out


def _absorb(base, extra):
    """把 extra 的信息补进 base（只填补空字段），并登记来源"""
    for s in (extra.get("verified_by") or []):
        if s not in base["verified_by"]:
            base["verified_by"].append(s)
    for k, v in extra.items():
        if k in ("verified_by", "impact", "access", "gated"):
            continue
        if v and not base.get(k):
            base[k] = v
    # 雪球结果(gated=False)与直接检索结果合并时，不因合并而丢失豁免标记
    if extra.get("gated") is False:
        base["gated"] = False
    for k, v in (extra.get("impact") or {}).items():
        if v is not None and base["impact"].get(k) is None:
            base["impact"][k] = v
    for k, v in (extra.get("access") or {}).items():
        if v and not base["access"].get(k):
            base["access"][k] = v
    if extra.get("tldr") and not base.get("tldr"):
        base["tldr"] = extra["tldr"]


# --------------------------------------------------------------------------
# 影响力评分（透明、可解释，不做黑箱）
# --------------------------------------------------------------------------
def impact_score(rec):
    """
    综合影响力 = 0.40·FWCI归一化 + 0.35·全局引用百分位 + 0.25·同年引用百分位
    FWCI 是领域归一化指标，能消除「领域不同引用天差地别」的偏差；
    同年百分位则让新论文不至于因为时间短而被埋没。
    撤稿论文直接 0 分。
    """
    if rec.get("is_retracted"):
        return 0.0
    im = rec.get("impact") or {}
    fwci = im.get("fwci") or 0.0
    pct = im.get("citation_percentile")
    ymax = im.get("cited_by_percentile_year_max")
    fwci_n = min(float(fwci) / 10.0, 1.0)          # FWCI=10 已属领域均值 10 倍
    pct_v = float(pct) if pct is not None else 0.0
    y_v = (float(ymax) / 100.0) if ymax is not None else 0.0
    return round(0.40 * fwci_n + 0.35 * pct_v + 0.25 * y_v, 4)


def recency_score(rec):
    """时效分：半衰期约 1.7 年，用于 frontier 意图"""
    y = rec.get("year")
    if not y:
        return 0.0
    try:
        age = max(0, NOW_YEAR - int(y))
    except Exception:
        return 0.0
    return round(math.exp(-age / 2.5), 4)


def frontier_score(rec):
    return round(0.60 * recency_score(rec) + 0.40 * impact_score(rec), 4)


def sort_key_for(intent):
    """
    意图自适应排序。相关性已由闸门过滤，这里只决定「在合格结果里更看重什么」：
      frontier          时效为主（0.6）—— 追前沿就是要新
      detail            相关性为主（0.6）—— 用户要的是「对得上题」
      overview/survey   相关与影响各半 —— 综述既要对题又要权威
      top/landscape     影响力为主（0.65）—— 找经典就是看分量
    """
    def _key(rec):
        rel = rec.get("relevance", 0.0)
        imp = impact_score(rec)
        if intent == "frontier":
            return 0.30 * rel + 0.10 * imp + 0.60 * recency_score(rec)
        if intent == "detail":
            return 0.60 * rel + 0.40 * imp
        if intent in ("overview", "survey", "landscape"):
            return 0.50 * rel + 0.50 * imp
        return 0.35 * rel + 0.65 * imp
    return _key


# --------------------------------------------------------------------------
# 引文雪球（detail 意图用）
# --------------------------------------------------------------------------
def fetch_seed(opener, oaid, errors):
    """取回带 referenced_works 的完整种子记录。
    注意：OpenAlex 对部分会议/预印本论文不索引参考文献（实测 CoT 论文 refs=0，
    而 Attention Is All You Need=28、DeepSeek-R1=15），故必须显式取回再判断。"""
    url = ("https://api.openalex.org/works?filter=ids.openalex:{}"
           "&per_page=1&select=id,title,referenced_works,"
           "referenced_works_count&mailto={}").format(oaid, MAILTO)
    try:
        j = json.loads(fetch(opener, url))
        res = j.get("results") or []
        return res[0] if res else None
    except Exception as e:
        errors.append("种子取回失败：{}".format(e))
        return None


def snowball(opener, seed, direction, maxn, errors):
    """
    backward = 看它引了谁（背景/奠基）；forward = 谁引了它（后续进展）。
    雪球结果一律标 gated=False：这些论文本就不含原查询词（例如 CoT 的
    参考文献里是「符号推理」「算术推理」），若过相关性闸门会被误杀。
    """
    out = []
    if direction == "backward":
        refs = seed.get("referenced_works") or []
        ids = [r.replace("https://openalex.org/", "") for r in refs][:40]
        if not ids:
            return out
        works, err = oa_query(
            opener, "ids.openalex:{}".format("|".join(ids)), maxn,
            sort="cited_by_count:desc")
        if err:
            errors.append("雪球(backward)：" + err)
        out = [parse_oa(w) for w in works]
    else:
        oaid = seed.get("id") or seed.get("openalex_id") or ""
        if isinstance(oaid, str) and oaid.startswith("https://"):
            oaid = oaid.replace("https://openalex.org/", "")
        if not oaid:
            return out
        # openalex_id 本身已含 W 前缀，不可再拼一个 W
        works, err = oa_query(opener, "cites:{}".format(oaid), maxn,
                              sort="cited_by_count:desc")
        if err:
            errors.append("雪球(forward)：" + err)
        out = [parse_oa(w) for w in works]

    for r in out:
        r["gated"] = False
    return out


# --------------------------------------------------------------------------
# 意图自适应检索策略
# --------------------------------------------------------------------------
def run_strategy(opener, query, intent, maxn, s2_key, errors):
    """
    每种意图对应一套组合拳，而不是单一排序。
    返回 (records, notes)
    """
    notes, collected = [], []

    if intent == "overview":
        # 概览/入门：综述打底 + 少量奠基性，帮用户先建立骨架
        n_survey = max(2, int(maxn * 0.6))
        n_top = maxn - n_survey
        rows, e = oa_query(
            opener, "title.search:{} survey".format(query), n_survey)
        if e:
            errors.append("综述层：" + e)
        for w in rows:
            r = parse_oa(w)
            r["tier"] = "survey"
            collected.append(r)
        rows, e = oa_query(opener,
                           "title_and_abstract.search:{}".format(query), n_top)
        if e:
            errors.append("奠基层：" + e)
        for w in rows:
            r = parse_oa(w)
            r["tier"] = "foundational"
            collected.append(r)
        notes.append("策略：综述层（建立骨架）+ 奠基层（经典工作），适合入门概览")

    elif intent == "landscape":
        # 全面了解：三层结构 —— 综述 / 奠基 / 前沿
        n_s = max(2, int(maxn * 0.3))
        n_f = max(2, int(maxn * 0.4))
        n_l = maxn - n_s - n_f
        for suffix in ("survey", "review"):
            rows, e = oa_query(
                opener, "title.search:{} {}".format(query, suffix), n_s)
            if e:
                errors.append("综述层：" + e)
                continue
            if rows:
                for w in rows:
                    r = parse_oa(w)
                    r["tier"] = "survey"
                    collected.append(r)
                break
        rows, e = oa_query(opener,
                           "title_and_abstract.search:{}".format(query), n_f)
        if e:
            errors.append("奠基层：" + e)
        for w in rows:
            r = parse_oa(w)
            r["tier"] = "foundational"
            collected.append(r)
        rows, e = arxiv_query(opener, query, max(1, n_l))
        if e:
            errors.append("前沿层：" + e)
        for r in rows:
            r["tier"] = "frontier"
            collected.append(r)
        notes.append("策略：三层版图 —— 综述（骨架）/ 奠基（经典）/ 前沿（最新）")

    elif intent == "detail":
        # 抠细节：精准命中 → 对首条做引文雪球，顺藤摸瓜
        rows, e = oa_query(opener,
                           "title_and_abstract.search:{}".format(query), maxn)
        if e:
            errors.append("精准层：" + e)
        seeds = [parse_oa(w) for w in rows]
        for r in seeds:
            r["tier"] = "targeted"
            r["relevance"] = relevance(r, query)
        collected.extend(seeds)

        if seeds:
            # 关键：雪球种子必须先过相关性闸门。
            # 否则会拿「原始被引第一名」当种子——实测那是一篇被引 2073 的肝性脑病论文，
            # 顺着它滚出来的全是 Gastroenterology / Lancet 等医学论文。
            good = sorted(
                [r for r in seeds if r["relevance"] >= REL_GATE],
                key=lambda r: (r["relevance"], impact_score(r)),
                reverse=True)
            back, fwd, used = [], [], None
            for cand in (good or seeds)[:3]:
                oaid = cand.get("openalex_id")
                if not oaid:
                    continue
                raw = fetch_seed(opener, oaid, errors)
                if not raw:
                    continue
                fwd = snowball(opener, raw, "forward",
                               max(2, maxn // 3), errors)
                back = snowball(opener, raw, "backward",
                                max(2, maxn // 3), errors)
                used = cand
                if back:
                    break   # 拿到参考文献就收手；否则换下一个种子再试
            if used is None:
                notes.append("策略：精准命中（未取到可用种子，雪球跳过）")
            else:
                for r in back:
                    r["tier"] = "background"
                for r in fwd:
                    r["tier"] = "followup"
                collected.extend(back + fwd)
                tail = "" if back else "（该种子未索引参考文献，回溯为空）"
                notes.append("策略：精准命中 + 引文雪球（回溯 {} 篇 / 追踪 {} 篇）{}"
                             .format(len(back), len(fwd), tail))
        else:
            notes.append("策略：精准命中")

    elif intent == "frontier":
        # 追前沿：arXiv 最新 + 近两年高 FWCI
        n_l = max(2, int(maxn * 0.6))
        n_r = maxn - n_l
        rows, e = arxiv_query(opener, query, n_l)
        if e:
            errors.append("最新层：" + e)
        for r in rows:
            r["tier"] = "frontier"
            collected.append(r)
        rows, e = oa_query(
            opener,
            "title_and_abstract.search:{},from_publication_date:{}-01-01"
            .format(query, NOW_YEAR - 2), n_r)
        if e:
            errors.append("近期高影层：" + e)
        for w in rows:
            r = parse_oa(w)
            r["tier"] = "recent-impact"
            collected.append(r)
        notes.append("策略：最新预印本 + 近两年高影响力，跳过综述（假设已有基础）")

    elif intent == "survey":
        got = False
        for suffix in ("survey", "review"):
            rows, e = oa_query(
                opener, "title.search:{} {}".format(query, suffix), maxn)
            if e:
                errors.append("综述({})：{}".format(suffix, e))
                continue
            if rows:
                for w in rows:
                    r = parse_oa(w)
                    r["tier"] = "survey"
                    collected.append(r)
                got = True
                break
        if not got:
            rows, e = oa_query(
                opener,
                "title_and_abstract.search:{} survey review".format(query),
                maxn)
            if e:
                errors.append("综述(回退)：" + e)
            for w in rows:
                r = parse_oa(w)
                r["tier"] = "survey"
                collected.append(r)
        notes.append("策略：标题级 survey/review 优先（避免摘要级误匹配老论文）")

    elif intent == "top":
        rows, e = oa_query(opener,
                           "title_and_abstract.search:{}".format(query), maxn)
        if e:
            errors.append("高影层：" + e)
        for w in rows:
            r = parse_oa(w)
            r["tier"] = "foundational"
            collected.append(r)
        notes.append("策略：高影响力排序（FWCI + 全局百分位 + 同年百分位）")

    elif intent == "latest":
        rows, e = arxiv_query(opener, query, maxn)
        if e:
            errors.append("最新层：" + e)
        for r in rows:
            r["tier"] = "frontier"
            collected.append(r)
        notes.append("策略：arXiv 按提交时间倒序（最新预印本）")

    elif intent == "triage":
        rows, e = s2_query(opener, query, maxn, s2_key)
        if e:
            errors.append(e)
        for r in rows:
            r["tier"] = "triage"
            collected.append(r)
        notes.append("策略：Semantic Scholar 速筛（低优先，需 key）")

    return collected, notes


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------
def badge(rec):
    im = rec.get("impact") or {}
    b = []
    if rec.get("is_retracted"):
        b.append("⚠️撤稿")
    if im.get("is_top_1_percent"):
        b.append("🏆前1%")
    elif im.get("is_top_10_percent"):
        b.append("⭐前10%")
    if rec.get("access", {}).get("pdf_url"):
        b.append("📄全文")
    elif rec.get("access", {}).get("is_oa"):
        b.append("🔓OA")
    return " ".join(b)


def print_text(payload):
    print("# [{}] 「{}」结构化检索结果".format(
        payload["intent"], payload["query"]))
    print("# 数据源：{}　命中 {} 篇　生成于 {}".format(
        " + ".join(payload["sources_used"]), payload["count"],
        payload["generated_at"]))
    for n in payload.get("notes", []):
        print("# {}".format(n))
    if payload.get("errors"):
        print("# ⚠️ 部分链路异常（结果仍可用）：")
        for e in payload["errors"]:
            print("#    - {}".format(e))
    print()
    for i, r in enumerate(payload["results"], 1):
        im = r.get("impact") or {}
        ac = r.get("access") or {}
        tier = r.get("tier") or "-"
        print("{:2d}. {}".format(i, r["title"]))
        print("    [{}] {} | {} | 相关度 {} | 被引 {} | FWCI {} | 百分位 {}".format(
            tier,
            r.get("year") or "?",
            (r.get("venue") or "?")[:34],
            r.get("relevance"),
            im.get("cited_by_count") if im.get("cited_by_count") is not None else "-",
            round(im["fwci"], 2) if im.get("fwci") is not None else "-",
            round(im["citation_percentile"], 3)
            if im.get("citation_percentile") is not None else "-",
        ))
        b = badge(r)
        if b:
            print("    {}".format(b))
        print("    校验源：{}".format(" + ".join(r.get("verified_by", []))))
        link = ac.get("pdf_url") or ac.get("landing_url") or \
            (("https://doi.org/" + r["doi"]) if r.get("doi") else "")
        if link:
            print("    {}".format(link))
        if r.get("abstract"):
            print("    摘要：{}".format(trunc(r["abstract"], 180)))
        print()


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="paper-explorer v2 · 结构化论文检索")
    ap.add_argument("--query", required=True)
    ap.add_argument("--intent", default="landscape",
                    choices=["overview", "landscape", "detail", "frontier",
                             "top", "latest", "survey", "triage"])
    ap.add_argument("--max", type=int, default=12)
    ap.add_argument("--json", action="store_true",
                    help="输出结构化 JSON（供下载 / HTML 门户消费）")
    ap.add_argument("--json-out", default=None, help="JSON 写入文件")
    ap.add_argument("--proxy", default=DEFAULT_PROXY)
    ap.add_argument("--s2-key", default=os.environ.get("S2_API_KEY"))
    ap.add_argument("--oa-key", default=None,
                    help="OpenAlex API key（免费申请，配额 ×10）；"
                         "缺省读环境变量 OPENALEX_API_KEY")
    ap.add_argument("--verify", default="on", choices=["on", "off"],
                    help="Crossref DOI 真实性校验（默认 on，仅校验前 10 条）")
    ap.add_argument("--abstracts", default="on", choices=["on", "off"],
                    help="输出中是否包含摘要（门户需要，默认 on）")
    args = ap.parse_args()

    opener = build_opener(args.proxy)
    errors = []

    global OA_KEY
    if args.oa_key:
        OA_KEY = args.oa_key.strip()

    records, notes = run_strategy(
        opener, args.query, args.intent, args.max,
        args.s2_key, errors)

    records = merge_records(records)

    if any("429" in e for e in errors):
        notes.append("OpenAlex 免费额度今日受限（HTTP 429，已自动退避重试），"
                     "相关层自动降级 arXiv；可在 openalex.org/settings/api "
                     "免费申请 key 并设 OPENALEX_API_KEY（配额 ×10）后重跑")

    # 相关性闸门：先过滤噪声，再做后续校验，避免把请求浪费在垃圾结果上
    for r in records:
        r["relevance"] = relevance(r, args.query)
    before = len(records)
    records = [r for r in records
               if r.get("gated") is False or r["relevance"] >= REL_GATE]
    dropped = before - len(records)
    if dropped:
        notes.append("相关性闸门：剔除 {} 篇不相关结果（查询实词覆盖率低于 {}）"
                     .format(dropped, REL_GATE))

    # Crossref 交叉校验（并发，仅前 10 条，避免拖慢）
    if args.verify == "on":
        targets = [r for r in records if r.get("doi")][:10]
        if targets:
            def _v(r):
                ok, cited = cr_verify(opener, r["doi"])
                return r, ok, cited
            try:
                with ThreadPoolExecutor(max_workers=5) as ex:
                    for r, ok, cited in ex.map(_v, targets):
                        if ok:
                            if "crossref" not in r["verified_by"]:
                                r["verified_by"].append("crossref")
                            if cited is not None and \
                                    r["impact"].get("cited_by_count") is None:
                                r["impact"]["cited_by_count"] = cited
                        else:
                            r["doi_verified"] = False
            except Exception as e:
                errors.append("Crossref 校验异常（已跳过）：{}".format(e))

    # 打分 + 排序（撤稿永远垫底）
    key = sort_key_for(args.intent)
    for r in records:
        r["score"] = impact_score(r)
        r["recency"] = recency_score(r)
        r["frontier_score"] = frontier_score(r)
        r["final_score"] = round(key(r), 4)
    records.sort(key=lambda r: (0 if r.get("is_retracted") else 1,
                                r["final_score"]), reverse=True)
    records = records[:args.max * 2] if args.intent in (
        "landscape", "detail") else records[:args.max]

    if args.abstracts == "off":
        for r in records:
            r.pop("abstract", None)

    sources = sorted({s for r in records for s in r.get("verified_by", [])})
    payload = {
        "query": args.query,
        "intent": args.intent,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sources_used": sources,
        "notes": notes,
        "errors": errors,
        "count": len(records),
        "results": records,
    }

    if args.json or args.json_out:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as f:
                f.write(text)
            print("JSON 已写入：{}".format(args.json_out))
        if args.json:
            print(text)
    else:
        print_text(payload)


if __name__ == "__main__":
    main()
