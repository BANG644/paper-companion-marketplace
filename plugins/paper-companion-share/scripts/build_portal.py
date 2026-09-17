#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
paper-library · HTML 论文门户生成器

把检索结果（library.json）渲染成一个单文件、自包含、响应式的 HTML 门户：
  · 顶部：主题 / 日期 / 数据源 / 统计概览
  · 工具栏：关键词过滤、分层筛选（综述/奠基/前沿…）、排序切换
  · 卡片流：每篇一卡，含影响力徽章（FWCI、前1%、被引、全文可得）
  · 详情弹层：完整摘要 + 关键信息 + PDF 内联预览（有全文时）或原文链接
  · 响应式：手机单列、平板双列、桌面多列；弹层在手机上全屏

设计约束：
  · 单文件自包含，CSS/JS/数据全内联，不依赖任何 CDN，可离线打开
  · 相对路径引用 ./pdfs/*.pdf，因此整个目录可原样部署到公网
  · <meta charset="utf-8"> + viewport，中文与移动端均正常
"""

import os
import json
import time
import argparse

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#f6f7f9; --card:#fff; --ink:#1a1d21; --muted:#5b6470; --line:#e3e7ec;
  --accent:#12b3a8; --accent-soft:#e6f7f6; --gold:#c98a12; --gold-soft:#fdf4e2;
  --red:#d8483f; --radius:14px; --shadow:0 1px 3px rgba(16,24,40,.06),0 6px 20px rgba(16,24,40,.05);
}
body{background:var(--bg);color:var(--ink);
  font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased;padding:0 0 60px}
.wrap{max-width:1240px;margin:0 auto;padding:0 20px}
header{background:linear-gradient(135deg,#0f766e,#12b3a8);color:#fff;padding:34px 0 30px;margin-bottom:26px}
header h1{font-size:26px;font-weight:650;letter-spacing:.2px;margin-bottom:8px;line-height:1.35}
header .sub{opacity:.92;font-size:14px;line-height:1.7}
header .meta{margin-top:14px;display:flex;flex-wrap:wrap;gap:8px}
.chip{background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.22);
  padding:4px 11px;border-radius:999px;font-size:12.5px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:12px;margin:22px 0 20px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px}
.stat b{display:block;font-size:22px;font-weight:660;color:var(--accent);line-height:1.3}
.stat span{font-size:12.5px;color:var(--muted)}
.bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:18px}
.bar input[type=search]{flex:1;min-width:200px;padding:10px 14px;border:1px solid var(--line);
  border-radius:10px;font-size:14px;background:var(--card);color:var(--ink);outline:none}
.bar input[type=search]:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.bar select{padding:10px 12px;border:1px solid var(--line);border-radius:10px;
  background:var(--card);font-size:14px;color:var(--ink)}
.tiers{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:18px}
.tier-btn{border:1px solid var(--line);background:var(--card);color:var(--muted);
  padding:6px 13px;border-radius:999px;font-size:13px;cursor:pointer;transition:.15s}
.tier-btn:hover{border-color:var(--accent);color:var(--accent)}
.tier-btn.on{background:var(--accent);border-color:var(--accent);color:#fff}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  padding:17px 18px;cursor:pointer;transition:.18s;display:flex;flex-direction:column;
  box-shadow:var(--shadow)}
.card:hover{transform:translateY(-2px);border-color:var(--accent);
  box-shadow:0 6px 22px rgba(18,179,168,.14)}
.card h3{font-size:15.5px;font-weight:620;line-height:1.5;margin-bottom:9px}
.card .who{font-size:12.5px;color:var(--muted);margin-bottom:10px}
.card .abs{font-size:13.3px;color:var(--muted);line-height:1.6;flex:1;
  display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.badges{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
.bg{font-size:11.5px;padding:3px 9px;border-radius:6px;background:#f1f3f6;color:var(--muted);white-space:nowrap}
.bg.hi{background:var(--gold-soft);color:var(--gold);font-weight:600}
.bg.ok{background:var(--accent-soft);color:var(--accent);font-weight:600}
.bg.warn{background:#fdeceb;color:var(--red);font-weight:600}
.bg.tier{background:#eef2ff;color:#4457c4}
.empty{text-align:center;color:var(--muted);padding:60px 20px;font-size:14px}
.mask{position:fixed;inset:0;background:rgba(17,24,39,.5);display:none;
  align-items:flex-start;justify-content:center;padding:34px 16px;z-index:50;overflow-y:auto}
.mask.on{display:flex}
.modal{background:var(--card);border-radius:16px;max-width:900px;width:100%;
  box-shadow:0 20px 60px rgba(16,24,40,.28);overflow:hidden;margin:auto 0}
.modal header{background:#0f766e;color:#fff;padding:18px 22px;margin:0}
.modal header h2{font-size:18px;font-weight:640;line-height:1.45}
.modal header .m-sub{margin-top:7px;font-size:13px;opacity:.93}
.body{padding:20px 22px}
.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:11px;margin-bottom:16px}
.kv div{background:var(--bg);border-radius:9px;padding:9px 12px}
.kv i{display:block;font-style:normal;font-size:11.5px;color:var(--muted);margin-bottom:2px}
.kv b{font-size:13.5px;font-weight:600}
.sect{margin-bottom:16px}
.sect h4{font-size:13px;color:var(--muted);font-weight:600;margin-bottom:7px;
  text-transform:uppercase;letter-spacing:.5px}
.sect p{font-size:14px;line-height:1.75;color:#333a44}
.pdfbox{border:1px solid var(--line);border-radius:10px;overflow:hidden;background:#fafbfc}
.pdfbox iframe{width:100%;height:70vh;border:0;display:block}
.nopdf{padding:22px;text-align:center;color:var(--muted);font-size:13.5px}
.links{display:flex;flex-wrap:wrap;gap:9px;margin-top:12px}
.links a{font-size:13px;color:var(--accent);text-decoration:none;
  border:1px solid var(--accent);padding:7px 13px;border-radius:8px;transition:.15s}
.links a:hover{background:var(--accent);color:#fff}
.close{position:sticky;top:0;float:right;margin:12px 14px 0 0;background:#fff;border:0;
  color:var(--ink);font-size:22px;line-height:1;cursor:pointer;width:34px;height:34px;
  border-radius:50%;box-shadow:0 2px 8px rgba(0,0,0,.14)}
footer{text-align:center;color:var(--muted);font-size:12.5px;margin-top:34px;line-height:1.8}
@media(max-width:700px){
  header{padding:24px 0 22px}header h1{font-size:20px}
  .wrap{padding:0 14px}
  .grid{grid-template-columns:1fr;gap:13px}
  .mask{padding:0}
  .modal{border-radius:0;min-height:100vh;max-width:100%}
  .pdfbox iframe{height:60vh}
  .kv{grid-template-columns:1fr 1fr}
  .card h3{font-size:15px}
}
"""

JS = """
var DATA = __DATA__;
var state = {q:'', tier:'all', sort:'final'};

function esc(s){return (s==null?'':String(s)).replace(/[&<>"]/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}

function tierLabel(t){
  return ({survey:'综述',foundational:'奠基',frontier:'前沿',recent-impact:'近期高影',
    targeted:'精准命中',background:'雪球·背景',followup:'雪球·后续',
    triage:'速筛'})[t] || (t||'—');
}

function badges(r){
  var im=r.impact||{}, ac=r.access||{}, out=[];
  if(r.is_retracted) out.push('<span class="bg warn">⚠️ 已撤稿</span>');
  if(im.is_top_1_percent) out.push('<span class="bg hi">🏆 引用前1%</span>');
  else if(im.is_top_10_percent) out.push('<span class="bg hi">⭐ 引用前10%</span>');
  if(im.cited_by_count!=null) out.push('<span class="bg">被引 '+im.cited_by_count+'</span>');
  if(im.fwci!=null) out.push('<span class="bg">FWCI '+im.fwci.toFixed(1)+'</span>');
  if(r.local_pdf) out.push('<span class="bg ok">📄 全文已下载</span>');
  else if(ac.is_oa) out.push('<span class="bg ok">🔓 开放获取</span>');
  out.push('<span class="bg tier">'+esc(tierLabel(r.tier))+'</span>');
  return out.join('');
}

function match(r){
  if(state.tier!=='all' && r.tier!==state.tier) return false;
  if(!state.q) return true;
  var q=state.q.toLowerCase();
  var hay=[r.title||'', r.abstract||'', (r.authors||[]).join(' '),
           r.venue||'', (r.topics||[]).join(' ')].join(' ').toLowerCase();
  return hay.indexOf(q)>=0;
}

function sorted(list){
  var f={
    final:function(r){return r.final_score||0;},
    impact:function(r){return r.score||0;},
    cited:function(r){return (r.impact&&r.impact.cited_by_count)||0;},
    year:function(r){return r.year||0;}
  }[state.sort];
  return list.slice().sort(function(a,b){return f(b)-f(a);});
}

function render(){
  var list = sorted(DATA.results.filter(match));
  var tiers = {};
  DATA.results.forEach(function(r){ tiers[r.tier]=(tiers[r.tier]||0)+1; });

  var th='<button class="tier-btn'+(state.tier==='all'?' on':'')+
         '" data-t="all">全部 '+DATA.results.length+'</button>';
  Object.keys(tiers).sort().forEach(function(t){
    th+='<button class="tier-btn'+(state.tier===t?' on':'')+'" data-t="'+esc(t)+'">'+
        esc(tierLabel(t))+' '+tiers[t]+'</button>';
  });
  document.getElementById('tiers').innerHTML=th;

  if(!list.length){
    document.getElementById('grid').innerHTML=
      '<div class="empty">没有匹配的论文，换个关键词试试。</div>';
    return;
  }
  document.getElementById('grid').innerHTML = list.map(function(r,i){
    var idx = DATA.results.indexOf(r);
    var abs = r.abstract ? esc(r.abstract).slice(0,240) : '（该来源未提供摘要）';
    return '<article class="card" data-i="'+idx+'">'+
      '<h3>'+esc(r.title)+'</h3>'+
      '<div class="who">'+esc((r.year||'—')+(r.venue?' · '+r.venue:''))+
        ((r.authors&&r.authors.length)?' · '+esc(r.authors[0])+
          (r.authors.length>1?' 等':''):'')+'</div>'+
      '<div class="abs">'+abs+'</div>'+
      '<div class="badges">'+badges(r)+'</div></article>';
  }).join('');
}

function openModal(i){
  var r = DATA.results[i], im=r.impact||{}, ac=r.access||{}, m=document.getElementById('modal');
  var pdf = r.local_pdf || ac.pdf_url || '';
  var pdfHtml = pdf
    ? '<div class="pdfbox"><iframe src="'+esc(pdf)+'" title="PDF 预览"></iframe></div>'
    : '<div class="nopdf">该论文暂无开放获取全文，可通过下方原文链接访问。</div>';
  var links=[];
  if(pdf) links.push('<a href="'+esc(pdf)+'" target="_blank" rel="noopener">在新标签打开 PDF</a>');
  if(ac.landing_url) links.push('<a href="'+esc(ac.landing_url)+
    '" target="_blank" rel="noopener">原文页面</a>');
  if(r.doi) links.push('<a href="https://doi.org/'+esc(r.doi)+
    '" target="_blank" rel="noopener">DOI</a>');
  if(r.arxiv_id) links.push('<a href="https://arxiv.org/abs/'+esc(r.arxiv_id)+
    '" target="_blank" rel="noopener">arXiv</a>');

  m.innerHTML =
    '<div class="modal">'+
      '<button class="close" id="cls">&times;</button>'+
      '<header><h2>'+esc(r.title)+'</h2>'+
      '<div class="m-sub">'+esc([r.year||'', r.venue||'',
        (r.authors||[]).slice(0,4).join(', ')].filter(Boolean).join(' · '))+'</div></header>'+
      '<div class="body">'+
        '<div class="kv">'+
          '<div><i>综合排序分</i><b>'+(r.final_score!=null?r.final_score:'—')+'</b></div>'+
          '<div><i>被引次数</i><b>'+(im.cited_by_count!=null?im.cited_by_count:'—')+'</b></div>'+
          '<div><i>FWCI 领域归一化</i><b>'+(im.fwci!=null?im.fwci.toFixed(2):'—')+'</b></div>'+
          '<div><i>引用百分位</i><b>'+(im.citation_percentile!=null?
              (im.citation_percentile*100).toFixed(1)+'%':'—')+'</b></div>'+
          '<div><i>类型 / 分层</i><b>'+esc(r.type||'—')+' · '+esc(tierLabel(r.tier))+'</b></div>'+
          '<div><i>校验来源</i><b>'+esc((r.verified_by||[]).join(' + '))+'</b></div>'+
        '</div>'+
        '<div class="sect"><h4>摘要</h4><p>'+
          (r.abstract?esc(r.abstract):'（该来源未提供摘要）')+'</p></div>'+
        ((r.topics&&r.topics.length)?'<div class="sect"><h4>主题</h4><p>'+
          esc(r.topics.join(' · '))+'</p></div>':'')+
        '<div class="sect"><h4>原文</h4>'+pdfHtml+
          '<div class="links">'+links.join('')+'</div></div>'+
      '</div></div>';
  document.getElementById('mask').classList.add('on');
  document.body.style.overflow='hidden';
  document.getElementById('cls').onclick=closeModal;
}

function closeModal(){
  document.getElementById('mask').classList.remove('on');
  document.body.style.overflow='';
}

document.getElementById('q').addEventListener('input',function(e){
  state.q=e.target.value.trim(); render();});
document.getElementById('sort').addEventListener('change',function(e){
  state.sort=e.target.value; render();});
document.getElementById('tiers').addEventListener('click',function(e){
  var t=e.target.getAttribute('data-t'); if(t){state.tier=t;render();}});
document.getElementById('grid').addEventListener('click',function(e){
  var c=e.target.closest('.card'); if(c) openModal(+c.getAttribute('data-i'));});
document.getElementById('mask').addEventListener('click',function(e){
  if(e.target.id==='mask') closeModal();});
document.addEventListener('keydown',function(e){
  if(e.key==='Escape') closeModal();});
render();
"""

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ · 论文门户</title>
<style>__CSS__</style>
</head>
<body>
<header>
  <div class="wrap">
    <h1>__TITLE__</h1>
    <div class="sub">__SUB__</div>
    <div class="meta">__META__</div>
  </div>
</header>
<div class="wrap">
  <div class="stats" id="stats">__STATS__</div>
  <div class="bar">
    <input type="search" id="q" placeholder="按标题 / 摘要 / 作者 / 主题过滤…">
    <select id="sort">
      <option value="final">综合排序</option>
      <option value="impact">影响力优先</option>
      <option value="cited">被引优先</option>
      <option value="year">年份优先</option>
    </select>
  </div>
  <div class="tiers" id="tiers"></div>
  <div class="grid" id="grid"></div>
  <footer>__FOOT__</footer>
</div>
<div class="mask" id="mask"><div id="modal"></div></div>
<script>__JS__</script>
</body>
</html>
"""


def build(data, out_dir, title=None, deploy_url=None):
    results = data.get("results", [])
    query = data.get("query", "")
    intent = data.get("intent", "")
    title = title or (query + " · 论文检索门户")

    years = [r.get("year") for r in results if r.get("year")]
    n_pdf = sum(1 for r in results if r.get("local_pdf"))
    n_top = sum(1 for r in results
                if (r.get("impact") or {}).get("is_top_1_percent"))
    n_oa = sum(1 for r in results if (r.get("access") or {}).get("is_oa"))

    intent_cn = {"overview": "概览入门", "landscape": "领域全景",
                 "detail": "细节深挖", "frontier": "前沿追踪",
                 "top": "奠基经典", "latest": "最新预印本",
                 "survey": "综述检索", "triage": "快速速筛"}.get(intent, intent)

    stats = [
        ("<b>{}</b><span>论文总数</span>".format(len(results))),
        ("<b>{}</b><span>已下载全文</span>".format(n_pdf)),
        ("<b>{}</b><span>开放获取</span>".format(n_oa)),
        ("<b>{}</b><span>引用前 1%</span>".format(n_top)),
    ]
    if years:
        stats.append(("<b>{}–{}</b><span>年份跨度</span>".format(
            min(years), max(years))))

    meta = [
        '<span class="chip">检索意图：{}</span>'.format(intent_cn),
        '<span class="chip">数据源：{}</span>'.format(
            " + ".join(data.get("sources_used", [])) or "—"),
        '<span class="chip">生成于 {}</span>'.format(
            data.get("generated_at", time.strftime("%Y-%m-%d %H:%M"))),
    ]

    foot = ("本门户由 <b>论文研读助手 (paper-companion)</b> 自动生成 · "
            "影响力指标来自 OpenAlex（FWCI 为领域归一化引用影响力）· "
            "所有条目均为真实检索结果，未作任何虚构")
    if deploy_url:
        foot += " · 在线地址：<a href='{}' style='color:var(--accent)'>{}</a>".format(
            deploy_url, deploy_url)

    js_data = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

    html = (HTML
            .replace("__TITLE__", title)
            .replace("__CSS__", CSS)
            .replace("__JS__", JS.replace("__DATA__", js_data))
            .replace("__SUB__", "基于结构化检索策略生成的方法论化论文清单，"
                                "可按关键词与分层筛选，点开任意一篇查看摘要与原文。")
            .replace("__META__", "".join(meta))
            .replace("__STATS__", "".join(
                '<div class="stat">{}</div>'.format(s) for s in stats))
            .replace("__FOOT__", foot))

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def main():
    ap = argparse.ArgumentParser(description="生成 HTML 论文门户")
    ap.add_argument("--input", required=True, help="library.json（下载后的数据）")
    ap.add_argument("--out-dir", required=True, help="输出目录（PDF 应已在 pdfs/ 下）")
    ap.add_argument("--title", default=None)
    ap.add_argument("--deploy-url", default=None, help="部署后的公网地址（写入页脚）")
    args = ap.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    p = build(data, args.out_dir, args.title, args.deploy_url)
    size = os.path.getsize(p)
    print("门户已生成：{}".format(p))
    print("大小：{:.1f} KB　论文：{} 篇".format(size / 1024, len(data.get("results", []))))


if __name__ == "__main__":
    main()
