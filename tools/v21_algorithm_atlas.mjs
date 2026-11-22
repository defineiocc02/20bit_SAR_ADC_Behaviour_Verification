// =============================================================================
// 文件名     : v21_algorithm_atlas.mjs
// 模块名     : 算法图册检查与离线出版
// 功能       : 把原生工程矢量图、Mermaid 流程、实际实现与测试绑定到同一图册。
// 设计说明   : 原生 SVG 本身可编辑。其余图使用调用方提供的 Mermaid 和浏览器排版。
//              单图 PDF 与 SVG 来自同一次排版，网页阅读不需要执行 Mermaid。
// =============================================================================
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import os from "node:os";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const directory = path.join(root, "docs", "algorithms");
const chapters = ["digital"];
// 矢量图和同一图的 PDF 使用相同的源标识，避免不同语言的标题造成重名。
const labels = {flow: "算法流程", state: "状态转换", sequence: "交易时序"};
const argv = process.argv.slice(2);
const read = (file) => fs.readFileSync(file, "utf8");
const sha = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex");
const escapeHtml = (s) => s.replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;");
const assert = (condition, message) => { if (!condition) throw new Error(message); };

// 来源表中的文字与 Markdown 共用一处。PDF 的本地链接改为原件所在的已核对提交，
// 包含页码或图号的片段保留；专利链接直接指向授权文本。
// 本地 Markdown 可以用相对路径核对原件；离开仓库阅读的 PDF 改指已核对的源文件。
// 显示用图号保留不动，使论文印刷页码与 PDF 页序都可由读者复查。
function sourceMarkup(value) {
  const pattern=/\[([^\]]+)\]\(([^)]+)\)/g;
  const base='https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/3057d2f/';
  let out='', end=0;
  for(const hit of value.matchAll(pattern)) {
    const raw=hit[2];
    const target=/^https?:/.test(raw)?raw:base+path.relative(root,path.resolve(directory,raw));
    out+=escapeHtml(value.slice(end,hit.index))+'<a href="'+escapeHtml(target)+'">'+escapeHtml(hit[1])+'</a>';
    end=hit.index+hit[0].length;
  }
  return out+escapeHtml(value.slice(end));
}

function option(name) {
  const i = argv.indexOf(name);
  if (i < 0 || !argv[i+1] || argv[i+1].startsWith("--")) return null;
  return argv[i+1];
}

// -----------------------------------------------------------------------------
// 1. 解析可编辑图源并核对范围。机制是否已实现沿用已有清单，不由图的存在决定。
// -----------------------------------------------------------------------------
function loadCards() {
  const index = read(path.join(directory,"README.md"));
  // 仅以本工程的生产清单确定审查范围。它同时是综合与顶层展开使用的输入。
  const sources = read(path.join(root,"rtl/filelists/files_adc_v21.f")).split(/\r?\n/)
    .map(s=>s.trim()).filter(s=>s && !s.startsWith("//"));
  assert(sources.length && sources.every(p=>/^rtl\/[A-Za-z0-9_/-]+\.sv$/.test(p)), "invalid production list");
  assert(new Set(sources).size === sources.length, "duplicate production source");
  // 逐个提取真实模块名，再从顶层沿实例走闭包。模块评论中的历史顶层不计入。
  const modules = new Map();
  for(const src of sources){
    const body=read(path.join(root,src)).replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g,"");
    const name=body.match(/\bmodule\s+(\w+)/)?.[1];
    assert(name && !modules.has(name),"invalid module declaration: "+src);
    modules.set(name,body);
  }
  const reachable=new Set();
  function walk(name){
    if(reachable.has(name))return;
    const text=modules.get(name);
    assert(typeof text==="string","missing v21 module: "+name);
    reachable.add(name);
    for(const child of modules.keys()){
      if(child!==name && new RegExp("\\b"+child+"\\s+(?:#\\s*\\([^;]*?\\)\\s*)?u_\\w+\\s*\\(").test(text)) walk(child);
    }
  }
  walk("adc_digital_ip_v21");
  assert(reachable.size===sources.length,"unreachable module in production list");
  // 卡片从稳定锚点开始；标题可为方便读者改写。
  // 只有从生产顶层可达的模块才计入闭包。测试台可作为来源链接，不能扩充生产清单。
  const cards=[], allRefs = new Set(), graphNames = new Set();
  for (const chapter of chapters) {
    const doc = read(path.join(directory,chapter+".md"));
    // 锚点命名用于跨章节引用和离线导航，同页重复会让读者跳错状态。
    const headings = [...doc.matchAll(/^<a id="([\w-]+)"><\/a>\s*\n## ([^\n]+)/gm)];
    const localIds = new Set();
    for (let i=0;i<headings.length;i++) {
      const match = headings[i]; const [,anchor,title] = match;
      assert(!localIds.has(anchor), "duplicate section "+chapter+"#"+anchor);
      localIds.add(anchor);
      const body = doc.slice(match.index+match[0].length, headings[i+1]?.index ?? doc.length);
      assert(index.includes("("+chapter+".md#"+anchor+")"), "unindexed algorithm "+anchor);
      const benefit = body.match(/^\*\*优势。\*\*\s*(.+)$/m)?.[1];
      const effect = body.match(/^\*\*效果与验证。\*\*\s*(.+)$/m)?.[1];
      // 公开披露与 RTL 决策分开写。每个主题均需说明它能追到的原图以及设计补全的位置。
      const original=body.match(/^\*\*原文对照。\*\*\s*(.+)$/m)?.[1];
      assert(original, "missing primary-source comparison: "+anchor);
      assert(benefit && [...benefit].length<=30 && effect, "missing purpose or evidence: "+anchor);

      // 只在图源以外解析链接，避免把流程节点语法误作文件名。
      const prose = body.replace(/^~~~(?:text|mermaid)\r?\n[\s\S]*?^~~~$/gm,"");
      const sourceRefs = [];
      for (const r of prose.matchAll(/(?<!!)\[[^\]\n]+\]\(([^)\s]+)\)/g)) {
        const href = r[1].split("#")[0];
        if (!href || /^https?:\/\//.test(href)) continue;
        const file = path.resolve(directory,decodeURIComponent(href));
        assert(file.startsWith(root+path.sep) && fs.existsSync(file), "missing reference "+href);
        const rel=path.relative(root,file);
        sourceRefs.push(rel); allRefs.add(rel);
      }
      assert(sourceRefs.some(p=>/\.(?:py|sv|sh|tcl|yml)$/.test(p)), "no implementation boundary: "+anchor);
      assert(sourceRefs.some(p=>/(?:^|\/)(?:tests?|tb|fv)\//.test(p) || /tb_\w+\.sv$/.test(p)), "no verification link: "+anchor);
      // 一张纯组合图与同一卡的状态图分开命名。未设状态机的算术不造新状态。
      const graphs = [], counts={};
      // 手工布线的框图直接链接到可编辑的 SVG。同一位置只接收一种源格式，
      // 不另外维护一份描述相同端口的流程代码，也不从渲染好的图片反推状态。
      const figures=/^~~~(?:text|mermaid)\r?\n((?:flowchart|stateDiagram-v2|sequenceDiagram)[\s\S]*?)\r?\n~~~$|^!\[[^\]\n]+\]\(figures\/([\w-]+)-(flow|state)\.svg "SVG 源图"\)$/gm;
      for (const bin of body.matchAll(figures)){
        const native=Boolean(bin[2]);
        const content=native ? read(path.join(directory,"figures",bin[2]+"-"+bin[3]+".svg")) : bin[1].trim();
        const kind=native ? bin[3] : content.startsWith("stateDiagram")?"state":content.startsWith("sequenceDiagram")?"sequence":"flow";
        counts[kind]=(counts[kind]??0)+1;
        const name=[chapter,anchor,kind].join("-")+(counts[kind]>1?"-"+counts[kind]:"");
        const link=native ? "(figures/"+name+'.svg "SVG 源图")' : "(figures/"+name+".svg)";
        assert(!graphNames.has(name) && prose.includes(link),"SVG link missing or duplicated: "+name);
        if(native) assert(bin[2]===chapter+"-"+anchor,"native diagram belongs to a different section");
        const caption=prose.split(link)[1]?.match(/^\s*\*\*图中重点。\*\*\s*([^\n]+)/)?.[1];
        assert(caption && [...caption].length<=30, "missing or overlong figure purpose: "+name);
        // 图中作用句按 Unicode 字符计数。多字节汉字不能用字节长度代替，英文缩写和标点也计入。
        // 图下仍有单独的数据、输入条件和文献说明，短句只承载主张。
        const originalLabels=Array.from(original.matchAll(/\[([^\]]+)\]\(/g),m=>m[1]).join(' · ');
        graphNames.add(name);graphs.push({name,kind,content,native,caption,originalLabels,hash:sha(content)});
      }
      assert(graphs.some(g=>g.kind==="flow"),"missing algorithm flow: "+anchor);
      // 印刷册也保留异常、优先级及边界说明；不重复图源和导航文字。
      const notes = prose.replace(/<details>[\s\S]*?<\/details>/g,"")
        .replace(/!?\[[^\]\n]+\]\([^)]+\)/g,"").split(/\n\s*\n/)
        .map(s=>s.trim().replace(/(?:\s*·\s*)+$/,"").trim())
        .filter(s=>s && !s.startsWith("**优势。") && !s.startsWith("**效果与验证。")
          && !s.startsWith("**原文对照。") && !s.startsWith("**图中重点。") && !s.startsWith("**实现**") && !s.startsWith("**验证**"))
        .map(s=>s.replaceAll("**","").replaceAll(String.fromCharCode(96),""));
      cards.push({chapter,anchor,title,benefit,effect,original,notes,graphs,sourceRefs});
    }
  }
  // 每份生产 RTL 都须能从算法卡片定位。独立组件与试验台不扩大设计闭包。
  // 手册允许用一张图覆盖共享逻辑，但不能跳过整个生产源文件。
  for(const src of sources) assert(allRefs.has(src),"production source missing from atlas: "+src);
  const anchors=new Set(cards.map(c=>c.chapter+".md#"+c.anchor));
  for(const hit of index.matchAll(/\]\((digital\.md#[a-z0-9-]+)\)/g))
    assert(anchors.has(hit[1]),"index points to missing algorithm: "+hit[1]);
  return {cards, sourceCount:sources.length};
}

// -----------------------------------------------------------------------------
// 2. 公开图的来源和字节身份。图源变化或图文件手改都会使只读检查失败。
// -----------------------------------------------------------------------------
function bookInputs() {
  // 正文或数值清单变化也会改变印刷册，即使单张状态图的箭头没有变化。
  return Object.fromEntries(["README.md","digital.md","figures/digital-evidence.json",
      "../../tools/run_static_linearity.py", "../figures/static_linearity/summary.json", "../figures/static_linearity/plot-data-0.0001.npz",
      "../figures/static_linearity/linearity-0.0001.svg", "../figures/static_linearity/effects-0.0001.svg",
      "../../references/ISSCC2024_PAPER.pdf", "../../references/ISSCC2024_SLIDES.pdf",
      "../mechanisms/figures/US10505561B2_FIG19_dither_injection.jpg", "../mechanisms/figures/US10511316B2_FIG19_two_stage_shuffler.jpg"]
    .map(file=>[file,sha(fs.readFileSync(path.join(directory,file)))]));
}

function checkAssets(cards) {
  const folder=path.join(directory,"figures");
  const record=JSON.parse(read(path.join(folder,"rendered.json")));
  // 已发布 PDF 的证据来自明确的 Git 对象。移动源码时仍核验原输入，
  // 当前导航另由 loadCards 核对。重新渲染产生的清单仍默认绑定当前树。
  const revision = record.atlas_inputs_revision;
  assert(!revision || /^[a-f0-9]{40}$/.test(revision), "invalid atlas input revision");
  const artifactDigest = (file) => sha(revision
    ? execFileSync("git", ["show", revision + ":" + path.relative(root, file)], {cwd: root, maxBuffer: 128 * 1024 * 1024})
    : fs.readFileSync(file));
  assert(record.atlas_pdf_sha256===sha(fs.readFileSync(path.join(directory,"digital-ip-atlas.pdf")))
         && Object.keys(record.atlas_inputs).join("\n")===Object.keys(bookInputs()).join("\n")
         && Object.entries(record.atlas_inputs).every(([name, digest]) =>
           artifactDigest(path.resolve(directory, name))===digest)
         && record.renderer_sha256===artifactDigest(fileURLToPath(import.meta.url)),
         "published PDF or its inputs differ from the atlas manifest");
  const evidence=JSON.parse(read(path.join(folder,"digital-evidence.json")));
  // “当前效果”绑定这些数字输入。换版后可用纯整数检查重建证据，无须启动 EDA。
  for(const [name,value] of Object.entries(evidence.source_sha256)){
    const file=path.resolve(root,name);
    assert(file.startsWith(root+path.sep) && artifactDigest(file)===value,"digital evidence input changed: "+name);
  }
  for(const [name,value] of Object.entries(evidence.figures)){
    // Git 中发布 SVG 和预览，PDF 收进本地论文插图包。
    if(name.endsWith(".pdf"))continue;
    assert(fs.existsSync(path.join(folder,name)) && sha(fs.readFileSync(path.join(folder,name)))===value,
           "digital evidence figure mismatch: "+name);
  }
  // 单独导出的八张数值图也须带原理句。中文的术语、字母、标点都计入长度。
  for(const [name,words] of Object.entries(evidence.figure_purposes)) {
    const isStatic=name==='effects'||name==='linearity';
    const file=isStatic?path.join(directory,'../figures/static_linearity',name+'-0.0001.svg'):path.join(folder,name+'.svg');
    const drawing=read(file);
    assert(words.zh && [...words.zh].length<=30 && (drawing.includes(words.zh)||drawing.includes(words.en)), 'missing or long purpose on '+name);
  }
  // 读首页的人看到的是这一笔的实际值。图中数字与 CI 导出的样本同时校验。
  const top=read(path.join(folder,"digital-transaction-flow.svg"));
  const example={...evidence.results.rtl.example,coarse:evidence.results.rtl.coarse};
  for(const key of ["word","coarse","k","raw","code"]){
    assert(top.includes('data-trace="'+key+'">'+example[key]+'</tspan>'),"wrong sample on the overview: "+key);
  }
  const graphs=cards.flatMap(c=>c.graphs);
  assert(Object.keys(record.diagrams).length===graphs.length,"rendered manifest count differs");
  // 两种摘要分别约束编辑图源和公开文件，防止只改文字却沿用旧箭头。
  for (const g of graphs) {
    const data=record.diagrams[g.name], file=path.join(folder,g.name+".svg");
    assert(data?.source_sha256===g.hash && data.caption===g.caption && fs.existsSync(file),"stale or missing diagram: "+g.name);
    assert(data.svg_sha256===sha(fs.readFileSync(file)),"SVG bytes changed: "+g.name);
  }
  // 接口名、已决顺序和示例掩码直接写在手工矢量图上；对应的 RTL 或向量
  // 变化时让作者重读这些图。渲染程序本身不能从数字位图推测应画哪条状态边。
  for (const card of cards) {
    for (const g of card.graphs.filter(graph=>graph.native)) {
      const saved=record.diagrams[g.name];
      const native = nativeInputs(card);
      const live = (name) => name.replace(/^rtl\/v21\//, "rtl/sar_core/")
        .replace(/^rtl\/(dither_pre|dem_mask_v3|sched18|recip_q60)\.sv$/, "rtl/sar_core/$1.sv");
      const recordedPaths = Object.keys(saved.inputs ?? {}).map(name => revision ? live(name) : name);
      assert(saved.source_format==="svg"
             && JSON.stringify(recordedPaths.sort())===JSON.stringify(Object.keys(native).sort())
             && Object.entries(saved.inputs ?? {}).every(([name, digest]) =>
                artifactDigest(path.resolve(root, name))===digest),
             "review native diagram against changed inputs: "+g.name);
    }
  }
}

function nativeInputs(card) {
  const refs=card.sourceRefs.filter(p=>p.startsWith("rtl/") || /^tb\/vec_/.test(p));
  return Object.fromEntries([...new Set(refs)].sort().map(p=>[p,sha(fs.readFileSync(path.join(root,p)))]));
}

// 完整书优先一次打印，保留浏览器生成的目录目的地和可搜索文字。部分 Chrome
// 在大幅矢量图与整本附录同时打印时会返回 Page.printToPDF: Printing failed。
// 此时按原有的正文/附录分页边界分印；PyPDF 合并页面对象、超链接和命名目的地，
// 不通过截图、缩小字体或删掉原件说明来绕过问题。
async function printBook(page, html, output) {
  const options={preferCSSPageSize:true,printBackground:true};
  const target=path.join(output,'atlas.pdf');
  await page.setContent(html,{waitUntil:'load'});
  await page.evaluate(()=>document.fonts.ready);
  const configuredPython=option('--pdf-python') || process.env.PDF_PYTHON;
  // 指定合并解释器的出版机直接分印；已有的无 Python 工作流仍可用本机 Chrome 一次导出。
  if(!configuredPython) {
    try {
      await page.pdf({...options,path:target});
      return;
    } catch(error) {
      if(!String(error).includes('Printing failed')) throw error;
    }
  }
  const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'adc-atlas-pdf-'));
  try {
    const selectors=['.appendix-nav,.appendix','.spread:not(.appendix):not(.appendix-nav)'];
    const parts=[];
    for(const [index,selector] of selectors.entries()) {
      // 从同一份已经签入的图和正文重建两段。附录内目录、实现链接与示意图保持在同一段。
      await page.setContent(html,{waitUntil:'load'});
      await page.locator(selector).evaluateAll(nodes=>nodes.forEach(n=>n.remove()));
      await page.evaluate(()=>document.fonts.ready);
      const file=path.join(temporary,index+'.pdf');
      await page.pdf({...options,path:file});
      parts.push(file);
    }
    const python=configuredPython || 'python3';
    // 正文页数和目录目的地属于对读要求。重新排版若让第 4 页脚溢出，合并阶段直接报错。
    // 原始论文和专利的 URI 注释保留到最终页对象；不把整页重新画成图片。
    const merge=`import sys
from pypdf import PdfReader, PdfWriter
front, appendix, target = sys.argv[1:]
if len(PdfReader(front).pages) != 4:
    raise ValueError('the four main spreads no longer fit their four pages')
writer = PdfWriter()
writer.append(front)
writer.append(appendix)
# 两段 HTML 均有 main 的首图锚点。分印会把附录目录的第一项指回附录本页。
# 修正唯一跨段目的地，其余十五项沿用附录原有的页内坐标和缩放。
names = writer.get_named_dest_root()
first = '/digital-transaction'
for i in range(0, len(names), 2):
    if str(names[i]) == first:
        del names[i:i+2]
        break
writer.add_named_destination(first, 0)
writer.add_outline_item('原理与数字证据', 0)
writer.add_outline_item('算法与状态机附录', 4)
writer.write(target)
writer.close()`;
    execFileSync(python,['-c',merge,...parts,target],{stdio:'inherit'});
  } finally {
    // 仅清理由此函数独占创建的两段临时 PDF，调用方给出的导出目录及原件保留。
    fs.rmSync(temporary,{recursive:true,force:true});
  }
}

// -----------------------------------------------------------------------------
// 3. 可选的本机出版。仅在显式 --render 时加载第三方运行时。
// -----------------------------------------------------------------------------
async function render(cards) {
  const dest=option("--out"), library=option("--mermaid-js");
  assert(dest && library,"--render requires --out and --mermaid-js");
  // 输出使用新目录，让中途失败的半套图片不能覆盖已经验收的图册。
  const output=path.resolve(dest);
  assert(!fs.existsSync(output),"output directory already exists");
  fs.mkdirSync(output,{recursive:true});
  for(const kind of ["svg","pdf","source"]) fs.mkdirSync(path.join(output,kind));
  const {chromium}=require(option("--playwright") || "playwright");
  const browser=await chromium.launch({headless:true,...(option("--browser")?{executablePath:option("--browser")}:{})});
  const signed={renderer_sha256:sha(fs.readFileSync(fileURLToPath(import.meta.url))),mermaid_bundle_sha256:sha(fs.readFileSync(library)),diagrams:{}};
  const assembled=[], navigation=[];
  try {
    // 在独立无头进程排版，渲染器不会打开用户浏览器的现有标签页。
    const page=await browser.newPage({viewport:{width:1450,height:1050}});
    // 单图 PDF 单独排版，图册的页边距不会压缩嵌入论文的矢量单图。
    const print=await browser.newPage();
    await page.setContent('<!doctype html><meta charset="utf-8"><body style="margin:0;background:white"></body>');
    await page.addScriptTag({path:path.resolve(library)});
    for (const card of cards) {
      const illustrations=[];
      for (const graph of card.graphs) {
        const data=await page.evaluate(async ({name,content,native,caption,originalLabels})=>{
          // 校验原生图的可视区域、可读标题及说明；原样发布路径与文字以支持矢量编辑。
          if(native){
            const doc=new DOMParser().parseFromString(content,"image/svg+xml");
            const svg=doc.documentElement;
            if(svg.localName!=="svg" || doc.querySelector("parsererror,script,foreignObject,image") ||
               !svg.querySelector("title") || !svg.querySelector("desc"))
              throw new Error("invalid native schematic: "+name);
            const {width,height}=svg.viewBox.baseVal;
            if(!(width>0 && height>0)) throw new Error("missing native viewBox: "+name);
            return {svg:content,width,height};
          }
          const font='Arial, "PingFang SC", "Noto Sans CJK SC", sans-serif';
          window.mermaid.initialize({startOnLoad:false,securityLevel:"strict",theme:"base",
            htmlLabels:false,fontFamily:font,deterministicIds:true,deterministicIDSeed:name,
            themeVariables:{fontFamily:font,fontSize:"17px",primaryColor:"#ffffff",
              primaryBorderColor:"#425b66",primaryTextColor:"#1b3441",secondaryColor:"#eef6f5",
              tertiaryColor:"#ffffff",lineColor:"#263e49",edgeLabelBackground:"#ffffff",
              mainBkg:"#ffffff",nodeBorder:"#425b66",textColor:"#1b3441",
              stateBkg:"#eef6f5",stateBorder:"#006d6a",labelColor:"#1b3441"},
            flowchart:{htmlLabels:false,curve:"linear",padding:11,nodeSpacing:24,rankSpacing:31,useMaxWidth:false},
            state:{useMaxWidth:false,nodeSpacing:25,rankSpacing:35}});
          const result=await window.mermaid.render("fig-"+name,content);
          const box=document.createElement("div");box.innerHTML=result.svg;document.body.append(box);
          await document.fonts.ready;
          const svg=box.querySelector("svg");
          // 状态边常穿过自己的文字底色。印刷版使用不透明白底保持箭头条件可读。
          for (const rect of svg.querySelectorAll("g.edgeLabel rect.background")) {
            rect.style.setProperty("fill", "#ffffff", "important");
            rect.style.setProperty("fill-opacity", "1", "important");
            rect.style.setProperty("opacity", "1", "important");
          }
          // 可编辑 SVG 独立打开时也可读取语义；PDF 与文档外显同一句图中重点。
          const desc=document.createElementNS("http://www.w3.org/2000/svg","desc");
          desc.textContent=caption;svg.prepend(desc);
          const v=svg.viewBox.baseVal;
          // 原理与出处按文本渲染进独立 SVG。只附在图册外围会使单独导出的图片失去工程边界。
          // 图注可能比狭长的流程更宽，测量真实文字宽度后再扩展画布。
          const credit=document.createElementNS(svg.namespaceURI,'text');
          credit.textContent='原图定位  '+originalLabels+'  ·  工程实现';
          credit.setAttribute('font-size','13');credit.setAttribute('fill','#285851');
          credit.setAttribute('text-anchor','middle');credit.setAttribute('y',String(v.y+v.height+20));
          svg.appendChild(credit);const mid=v.x+v.width/2;credit.setAttribute('x',String(mid));
          // 强调色由每图的 Mermaid 源指定，绘制器只排版，不猜测哪条边属于例外条件。
          const purpose=document.createElementNS(svg.namespaceURI,'text');
          purpose.textContent='作用　'+caption;purpose.setAttribute('class','svg-purpose');
          purpose.setAttribute('x',String(mid));purpose.setAttribute('y',String(v.y+v.height+40));
          purpose.setAttribute('text-anchor','middle');purpose.setAttribute('font-size','15');
          purpose.setAttribute('fill','#142f40');svg.appendChild(purpose);
          const width=Math.max(v.width,credit.getBBox().width+16,purpose.getBBox().width+16),height=v.height+48;
          if (!(width>0&&height>0)||svg.querySelector(".error-icon")) throw new Error("invalid SVG "+name);
          svg.setAttribute('viewBox',[mid-width/2,v.y,width,height].join(' '));
          svg.removeAttribute("style");svg.setAttribute("width",String(width));svg.setAttribute("height",String(height));
          const serial=new XMLSerializer().serializeToString(svg);
          box.remove();return {svg:serial,width,height};
        },graph);
        // 序列化后保存真实文本及矢量线条，不用栅格截图代替源码图。
        const svgPath=path.join(output,"svg",graph.name+".svg");
        fs.writeFileSync(svgPath,graph.native ? data.svg : data.svg+"\n");
        // 原生图的出版 SVG 就是原稿。仅 Mermaid 需要另附源文件。
        if(!graph.native) fs.writeFileSync(path.join(output,"source",graph.name+".mmd"),graph.content+"\n");
        // 物理页面采用最大双栏宽，宽或高受限时等比缩放。保留 PDF 内的文字与曲线。
        const scale=Math.min(500/data.width,505/data.height,1);
        const width=Math.ceil(data.width*scale)+8,height=Math.ceil(data.height*scale)+8;
        await print.setContent('<!doctype html><meta charset="utf-8"><style>@page{size:'+width+'pt '+height+
          'pt;margin:0}html,body{margin:0}svg{position:fixed;inset:4pt;width:'+(width-8)+'pt;height:'+(height-8)+'pt}</style>'+data.svg);
        await print.evaluate(()=>document.fonts.ready);
        await print.pdf({path:path.join(output,"pdf",graph.name+".pdf"),preferCSSPageSize:true,printBackground:true});
        signed.diagrams[graph.name]={source_format:graph.native?"svg":"mermaid",source_sha256:graph.hash,svg_sha256:sha(fs.readFileSync(svgPath)),
          width:data.width,height:data.height,print_scale:scale,caption:graph.caption};
        if(graph.native) signed.diagrams[graph.name].inputs=nativeInputs(card);
        illustrations.push('<figure class="'+(graph.native?'native':'mermaid')+'">'+data.svg+
          '<figcaption>'+escapeHtml(graph.caption)+'</figcaption></figure>');
      }
      const addr=card.chapter+'-'+card.anchor;
      const refs=[...new Set(card.sourceRefs)].filter(s=>!s.startsWith("docs/algorithms/figures/"));
      const base="https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/merge/adi-repro/";
      const links=refs.map(s=>'<a href="'+base+encodeURI(s)+'">'+escapeHtml(path.basename(s))+'</a>');
      assembled.push('<section class="card" id="'+addr+'"><h2>'+escapeHtml(card.title)+'</h2><p><b>功能　</b>'+
        escapeHtml(card.benefit)+'</p><p><b>验证　</b>'+escapeHtml(card.effect)+'</p>'+
        illustrations.join('')+card.notes.map(n=>'<p class="detail">'+escapeHtml(n)+'</p>').join('')+
        '<p class="source-detail"><b>原文对照　</b>'+sourceMarkup(card.original)+'</p><p class="refs">实现与核对　'+links.join(' · ')+'</p></section>');
      navigation.push('<a href="#'+addr+'">'+escapeHtml(card.title)+'</a>');
    }
    // 阅读顺序跟随信号与证据。两张互相解释的图放在同页，定量图的标题和来源由图本身携带。
    const evidence=JSON.parse(read(path.join(directory,"figures/digital-evidence.json")));
    const sourceBase="https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/3057d2f/";
    const master="references/ISSCC2024_PAPER.pdf", slides="references/ISSCC2024_SLIDES.pdf";
    const patD="docs/mechanisms/figures/US10505561B2_FIG19_dither_injection.jpg";
    const patM="docs/mechanisms/figures/US10511316B2_FIG19_two_stage_shuffler.jpg";
    const patent=n=>'https://patents.google.com/patent/'+n+'/en';
    const link=(url,label)=>'<a href="'+escapeHtml(url)+'">'+escapeHtml(label)+'</a>';
    const paper=(n,label)=>link(sourceBase+master+'#page='+n,label);
    const talk=(n,label)=>link(sourceBase+slides+'#page='+n,label);
    // 本地 HTML 展开论文、讲稿或授权专利的已有原件。公开 PDF 仅排本工程简图、
    // 图号和链接。这样打印页的证据说明与离线读者点开的真正原件明确区分。
    // 导出以目标目录为根，文件名取原件 basename。复制前只使用仓库中清单明确列出的来源。
    // 确保把 HTML 拷给他人时能一并带上已核对原件，在线 URL 继续供 PDF 阅读器使用。
    const support=path.join(output,'originals');fs.mkdirSync(support);
    for(const n of [master,slides,patD,patM]) fs.copyFileSync(path.join(root,n),path.join(support,path.basename(n)));
    const previews=new Map();
    function originalView(items){
      return '<details class="local-only"><summary>展开对照原图、原文</summary><div class="original-view">'+items.map(([file,page,caption])=>{
        const leaf=path.basename(file), rel='originals/'+leaf+(page?'#page='+page:'');
        let src=rel;
        if(page) {
          const stem=leaf.replace(/\.pdf$/i,'')+'-p'+page;
          if(!previews.has(stem)) {
            // 预览直接来自指定原件页，保留页内文字、图号和单位；不以重画示意冒充原件。
            // 老浏览器缺少内嵌 PDF 阅读器时仍能查看整页。缺少 Poppler 则保留系统 PDF 预览。
            try {
              execFileSync('pdftoppm',['-f',String(page),'-l',String(page),'-scale-to','1400',
                '-singlefile','-png',path.join(support,leaf),path.join(support,stem)],{stdio:'pipe'});
              previews.set(stem,'originals/'+stem+'.png');
            } catch(error) {
              if(error.code!=='ENOENT') throw error;
              previews.set(stem,null);
            }
          }
          src=previews.get(stem);
        }
        const view=src?'<a href="'+rel+'"><img src="'+src+'" alt="'+escapeHtml(caption)+'"></a>':
          '<object type="application/pdf" data="'+rel+'">'+link(rel,caption)+'</object>';
        return '<figure><figcaption>'+link(rel,caption)+'</figcaption>'+view+'</figure>';
      }).join('')+'</div></details>';
    }
    // 时钟沿和错码计数来自数字退休记录，不能借用静态模型给它们推算芯片精度。
    // 反过来，原始纸面性能也只在原文框中引用，不作为本 RTL 的运行结果。
    const measured=evidence.results.rtl;
    const archived=JSON.parse(read(path.resolve(directory,'../figures/static_linearity/summary.json')));
    const linearCase=archived.cases.find(entry=>entry.relative_mismatch_sigma===0.0001);
    // 不同片组混合后的全码宽度与单状态过渡单调性是不同判据。
    // 原文对照保留单状态交界回退数，防止把端点 INL 的改善写成整机单调证明。
    const boundary=linearCase.modes.both.decoders.fitted.coarse_boundary_checks;
    // 原件能确认架构、分路、选择方向和校正分工。器件连接的实例编号来自相应专利。
    // 写门、各 tag 的沿序、码宽和定点缩放按本工程的输入/观测记录解释。
    const comparison={
      sample:[
        ['原图上的三处连接',link(patent('US10505561B2'),'US10505561B2 图 19')+' 中，300 将低段送入 ADC 200，222 将全字送入采样 DAC 220，224 调整粗码。正文的七片实施例使用 f/7、c−U。'+talk(17,'作者讲稿第 17 页')+' 同时画出两路模拟注入。'],
        ['本图的一笔算术','八槽选用 f/8、c+U。名义条件下 16(Wm−Wp) = k−T = 206−6.625 = c−L。相同字在重构端漏扣一次 Wp 时多 13,568 码，重复扣则少 13,568 码。'+link('https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/3057d2f/docs/audit/TREE_AUDIT_REPORT.md#dither-word-audit','重构模块负对照')],
      ],
      timing:[
        ['原图中的相序',paper(2,'论文图 9.8.1、9.8.2')+' 画出两组交替采集、转换与余差采样。'+link(patent('US10516408B2'),'US10516408B2 图 16')+' 画出两组各八片、两片备用与共享 RA、ADC2。'],
        ['本工程的时钟沿','三个标签的事件如下。输入采样、余差捕获和 ADC2 返回可以同沿，原码与沿前登记的 tag 配对。512 回退及 640 MHz 为本工程实现和试验条件。'],
      ],
      weights:[
        ['原图中的选择',paper(2,'论文图 9.8.3 下部')+' 和'+talk(29,'讲稿第 29 页')+' 给出三位横向、纵向选择。'+link(patent('US10511316B2'),'US10511316B2 图 19、20')+' 中的 350、352 与 MUX 展示保留列分配。'],
        ['校正机制与工程量值',paper(1,'论文第 182 页')+' 说明片外取得系数、片上修正 DAC 权重。图中的一处 1/1024 偏差为定向激励：ADC2 原码相差 64，最终输出相差 32 码。24/192 是这组输入的结果。'],
      ],
      static:[
        ['原件的单位',paper(1,'论文第 182 页')+' 以 ppm 给出样片 INL +2.2/−1.8、DNL ±0.27。'+paper(2,'图 9.8.4')+' 的纵轴和'+talk(32,'讲稿第 32 页')+' 使用 LSB（20 位）。本图保留端点 LSB20；1 ppm FS = 1.048576 LSB20。'],
        ['模型的覆盖范围','训练 '+linearCase.training.training_samples.toLocaleString('en-US')+' 笔，评估 '+archived.states+' 个开关状态。全开、拟合表时，单状态粗码交界中有 '+boundary.reversed.toLocaleString('en-US')+'/'+boundary.total.toLocaleString('en-US')+' 处回退，最大 '+boundary.largest_drop+' 码。混合码宽曲线不单独证明逐状态单调。'],
      ],
    };
    // 每页选择可直接说明本页问题的原件。讲稿页按 PDF 页序，专利图用授权文献图号。
    // 源页图片仅由本地展开器读取；印刷版保留本文重绘的关系和原文入口。
    // 本地目录使用完整 PDF，所以论文页按阅读器页号标注。稿本上的 p.182 对应本地 PDF 第 1 页。
    const originals={sample:[[slides,17,'作者讲稿 p.17 · 分路原图'],[patD,0,'US10505561B2 · 图 19']],
      timing:[[master,2,'论文 p.183 · 图 9.8.1–2'],[slides,10,'讲稿 p.10 · 18 片池']],
      weights:[[slides,29,'讲稿 p.29 · 两个方向的选择'],[patM,0,'US10511316B2 · 图 19']],
      static:[[master,2,'论文 p.183 · 图 9.8.4'],[slides,32,'讲稿 p.32 · 样片曲线与单位']]};
    // 前三级标签是同一连续试验的最早三笔。列按观测顺序排列，不把采样与输出混成同一沿。
    // 若宏延迟变化，这张表必须从新观测填入，而不能手工平移旧表中的某一行。
    // 采样后有数字流水延迟，raw_edge 与 out 分为两列。表中的数值是时钟沿序号，不能当作 ns。
    const timingTable='<table class="trace"><tr><th>tag</th><th>采样</th><th>转换启动</th><th>末次写入</th><th>余差捕获</th><th>细码返回</th><th>输出</th></tr>'+
      measured.cadence.map(row=>'<tr>'+['tag','sample','start','post','capture','raw_edge','out'].map(k=>'<td>'+row[k]+'</td>').join('')+'</tr>').join('')+'</table>';
    function band(id) {
      return '<div class="comparison">'+comparison[id].map(([heading,body])=>'<div><b>'+heading+'</b><p>'+body+'</p></div>').join('')+'</div>'+(id==='timing'?timingTable:'');
    }
    const ci="https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/runs/"+evidence.results.rtl.run;
    const cite='<a href="'+ci+'">顶层 RTL 逐笔记录 · 3057d2f</a>';
    // 只有经过数字清单签名的插图才能按该图名嵌入。静态包络、原始 PDF 的校验在 bookInputs 中。
    function plate(file,alt) {
      const bytes=fs.readFileSync(path.resolve(directory,file));
      const digest=evidence.figures[path.basename(file)];
      if(digest) assert(sha(bytes)===digest,"changed evidence plate: "+file);
      // 独立 SVG 的图形 ID 不会串扰。使用矢量文件而非页面截图。
      return '<figure class="plate"><img alt="'+escapeHtml(alt)+'" src="data:image/svg+xml;base64,'+bytes.toString('base64')+'"></figure>';
    }
    // 前四页依次回答单笔分路、并行调度、物理开关与校正、全码线性度；相邻面板的证据性质分别说明。
    // 前三页面向数字集成，第四页引用归档电荷模型，目的是检查指标边界是否被正确解释。
    // 零误差是去扰成功的观测，不能为扩大差异单独裁掉越窗样本。权重对照按原码和片号配对。
    const topics=[
      ['sample','一笔转换与扰动扣回','顺着 tag 54 分开的两条线看，最后从同一笔的采样、转换开关取回权重。',
       [['figures/digital-transaction-flow.svg','从受理、两路预置到重构的实际数字样本'],['figures/dither-transfer.svg','64 个字的零误差及 SADC 断线反例']],cite],
      ['timing','同沿交接与逐位写门','沿竖线比较三个 tag，再沿第二行找到有比较而没有开关写入的那一拍。',
       [['figures/capture-handover.svg','三个标签共用一个边沿'],['figures/sar-events.svg','普通与两条保护路径的真实事件']],cite],
      ['weights','物理单元的选择与加权校正','先看同一命令如何分摊给不同单元，再看一个有偏差的单元如何改变最终码。',
       [['figures/physical-masks.svg','同一位置在固定和轮换时的使用次数'],['figures/integer-arithmetic.svg','192 笔按输入与开关配对的权重对照']],
       '开关覆盖来自归档叶子输出。权重图的系数已知，由理想宏注入单元偏差。 · '+cite],
      ['static','全码 INL 与 DNL','保持器件不变，先在同一组开关条件下换表，再比较四种工作方式。',
       [['../figures/static_linearity/linearity-0.0001.svg','同条件全码域端点线性度'],['../figures/static_linearity/effects-0.0001.svg','四种工况和局部单码宽度']],
       '静态电荷模型 · 器件种子 20261105 · 6 V 满幅 · 单码 5.722 µV · 评估关闭随机 RA 噪声。']
    ];
    // 主图页脚标识其来源层级；其中定量值或状态机通过仅说明给定激励和宏响应的结果。
    const front=topics.map(([id,title,lead,images,source],i)=>'<section class="spread" id="'+id+'"><div class="folio">'+
      '<span>20 位两级 SAR ADC 数字 IP</span><span>'+String(i+1).padStart(2,'0')+' / 04</span></div><h1>'+title+'</h1><p class="lead">'+lead+
      '</p>'+images.map(([file,alt])=>plate(file,alt)).join('')+band(id)+'<p class="source">'+source+'</p>'+originalView(originals[id])+'</section>').join('');
    const intro=read(path.join(directory,"README.md"));
    // 厂家的示意图只说明版式设计取法，链接与 ADC 原理论据在不同位置显示。
    const external=[...intro.matchAll(/\[([^\]\n]*(?:原图源码|状态机原图))\]\((https:\/\/[^)]+)\)/g)]
      .map(([,label,url])=>'<a href="'+escapeHtml(url)+'">'+escapeHtml(label)+'</a>');
    const toc='<section class="spread appendix-nav"><div class="folio">数字 IP · 工程附录</div><h1>逐模块查算法与状态</h1>'+
      '<p class="lead">青绿色强调决定结果的节点，暖色强调关闭写门的分支。每幅图下方说明关键条件和观测量。</p><nav>'+
      navigation.join('')+'</nav>'+plate('figures/slice-allocation.svg','调度冻结向量与统计的不同分母')+
      '<p class="source">绘图参考　'+external.join(' · ')+'</p></section>';
    // 附录保留工程细节和原有锚点。短算术链可并页，较大的原生框图独占版心。
    // 目录指向稳定的章节锚点。总框图已在第一页给出，附录不再重复同一份图片。
    // 短算术图可以并页，但每个带寄存器的算法仍保留独立的状态说明和验证入口。
    const byAnchor=new Map(cards.map((card,i)=>[card.anchor,assembled[i]]));
    const groups=[['config'],['schedule'],['ownership'],['pre'],['events'],['dem'],['bridge','physical-route'],
      ['sum','arming'],['capture'],['reciprocal','correction'],['reconstruct','round96']];
    // 若修改来源，需要在每幅独立成图和卡片正文两处重新生成，单改图册目录不足以完成发布。
    const detail=groups.map(group=>'<section class="spread appendix '+(group.length>1?'pair':'single')+'"><div class="folio">数字 IP · 工程附录</div>'+
      group.map(k=>byAnchor.get(k)).join('')+'</section>').join('');
    // 主图在固定栏宽排版。每页成对的两幅图共享问题，但图注分别保留分母、原文出处和工程假设。
    // 纸面关闭可交互控件，线性度包络仍使用 SVG，不将稠密码段重新光栅化。
    // 附录图的可见重点由正文提取；已着色的节点在无彩色打印时仍保留条件文字。
    const css=`
      *{box-sizing:border-box}
      body{margin:0;background:#edf2f1;color:#142f40;font:15px/1.5 Arial,"PingFang SC","Heiti SC",sans-serif}
      main{max-width:900px;margin:auto}
      a{color:#006e68;text-decoration:none}
      .spread{background:white;padding:38px 48px;margin:18px 0}
      .folio{border-bottom:1px solid #9db1b8;display:flex;justify-content:space-between;padding-bottom:5px;color:#435f6a;font-size:10pt}
      .spread h1{margin:8px 0 2px;font-size:24pt;line-height:1.2}
      .lead{margin:4px 0 12px;color:#405d69}
      .plate{margin:9px 0}
      .plate img{width:100%;height:auto;display:block}
      .source,.refs{color:#45616c;font-size:8pt;border-top:1px solid #d1dedd;padding-top:5px}
      .appendix-nav nav{columns:2;margin:12px 0}
      .appendix-nav nav a{display:block;margin:3px 0}
      .card h2{font-size:16pt;line-height:1.2;margin:10px 0 6px}
      .card p{margin:5px 0}
      .card figure{margin:14px 0 8px;text-align:center}
      .card svg{display:block;max-width:100%;height:auto;max-height:410px;margin:0 auto}
      .card figure.native svg{width:100%;max-height:none}
      .card .svg-purpose{visibility:hidden}
      .card figcaption{color:#20544e;background:#eff6f4;border-left:2px solid #00756b;text-align:left;margin:5px 0 0;padding:3px 7px;font-size:9pt}
      .card .detail{font-size:9pt;color:#29444f}
      .pair .card+.card{border-top:1px solid #9db1b8;margin-top:13px}
      .comparison{display:grid;grid-template-columns:1fr 1fr;gap:5mm;border-top:1px solid #a4babd;padding-top:2mm;margin-top:1.5mm;font-size:8.5pt;color:#2a4656}
      .comparison b{color:#005e58}
      .comparison p{margin:1mm 0}
      .trace{border-collapse:collapse;width:100%;font-size:7.6pt;margin:1mm 0}
      .trace th,.trace td{text-align:center;border-bottom:1px solid #dbe4e3;padding:1px 3px}
      .trace th{color:#345650}
      .local-only summary{cursor:pointer;color:#006e68}
      .original-view{display:grid;grid-template-columns:1fr 1fr;gap:12px}
      .original-view figure{margin:5px}
      .original-view object,.original-view img{width:100%;height:440px;object-fit:contain}
      .source-detail{font-size:8.6pt;color:#31574f}
      .card .refs{font-size:7.4pt;overflow-wrap:anywhere}
      @page{size:A4;margin:11mm 13.5mm 11mm}
      @media print {
        .local-only{display:none}
      .comparison{font-size:8pt;line-height:1.35}
      .source-detail{font-size:8pt}
      body{background:white;font-size:9.3pt;line-height:1.35}
      main{max-width:none}
      .spread{margin:0;padding:0;break-before:page}
      .spread:first-child{break-before:auto}
      .spread h1{font-size:21pt}
      .folio{font-size:8pt}
      .lead{margin:3pt 0 8pt}
      .source{font-size:7.5pt}
      .plate{margin:3pt 0;break-inside:avoid}
        #static .plate{width:95%;margin:0 auto}
      .card figure{break-inside:avoid;margin:5pt 0}
      .card figure svg{max-height:90mm}
      .card figure.native svg{width:100%;max-height:90mm}
      .card figcaption{font-size:8.3pt}
      .card .detail{font-size:8.5pt}
      .card h2{font-size:14pt}
      .pair .card figure svg{max-height:56mm}
      .pair .card .detail{font-size:8pt}
      .pair .card p{margin:2pt 0}
      .appendix-nav nav{font-size:10pt}
      .appendix-nav .plate{margin-top:13mm}
      }
    `;
    // 核心图和实测数据随 HTML 本身保存；一同导出的 originals 目录只供展开原件。
    // 将展示 HTML 移走时应该连同该目录移动，否则离线原件面板将缺少 PDF。
    const html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>20 位 SAR ADC 数字 IP · 实测、算法与状态机</title><style>'+css+'</style><main id="digital-transaction">'+front+toc+detail+'</main></html>';
    fs.writeFileSync(path.join(output,"atlas.html"),html);
    // 原件浏览器含独立 PDF 文档。即使 CSS 已隐藏，Chrome 打印仍可能接管子框架。
    // 印刷用 DOM 从源 HTML 去掉这些可展开的控件；图旁依据、原件链接和图号完整保留。
    const printHtml=html.replace(/<details class="local-only">[\s\S]*?<\/details>/g,'');
    await printBook(print,printHtml,output);
    signed.atlas_inputs=bookInputs();
    signed.atlas_pdf_sha256=sha(fs.readFileSync(path.join(output,"atlas.pdf")));
    fs.writeFileSync(path.join(output,"svg","rendered.json"),JSON.stringify(signed,null,2)+"\n");
  } finally {await browser.close();}
}

const {cards,sourceCount}=loadCards();
if (argv.includes("--render")) await render(cards);
else if (argv.length===0 || (argv.length===1 && argv[0]==="--check")) checkAssets(cards);
else throw new Error("use --check or --render --out DIR --mermaid-js JS [--playwright MODULE] [--browser EXE] [--pdf-python PYTHON]");
const graphs=cards.flatMap(c=>c.graphs);
console.log(cards.length+" algorithms, "+graphs.filter(g=>g.kind==="flow").length+" flows, "+
  graphs.filter(g=>g.kind==="state").length+" state diagrams, "+sourceCount+" production RTL sources");
