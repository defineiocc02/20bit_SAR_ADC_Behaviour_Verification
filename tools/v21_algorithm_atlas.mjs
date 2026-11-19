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
  const sources = read(path.join(root,"flow/build/filelists/files_adc_v21.f")).split(/\r?\n/)
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
      assert(benefit && effect, "missing purpose or evidence: "+anchor);

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
        graphNames.add(name);graphs.push({name,kind,content,native,hash:sha(content)});
      }
      assert(graphs.some(g=>g.kind==="flow"),"missing algorithm flow: "+anchor);
      // 印刷册也保留异常、优先级及边界说明；不重复图源和导航文字。
      const notes = prose.replace(/<details>[\s\S]*?<\/details>/g,"")
        .replace(/!?\[[^\]\n]+\]\([^)]+\)/g,"").split(/\n\s*\n/)
        .map(s=>s.trim().replace(/(?:\s*·\s*)+$/,"").trim())
        .filter(s=>s && !s.startsWith("**优势。") && !s.startsWith("**效果与验证。")
          && !s.startsWith("**实现**") && !s.startsWith("**验证**"))
        .map(s=>s.replaceAll("**","").replaceAll(String.fromCharCode(96),""));
      cards.push({chapter,anchor,title,benefit,effect,notes,graphs,sourceRefs});
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
  return Object.fromEntries(["README.md","digital.md","figures/digital-evidence.json"]
    .map(file=>[file,sha(fs.readFileSync(path.join(directory,file)))]));
}

function checkAssets(cards) {
  const folder=path.join(directory,"figures");
  const record=JSON.parse(read(path.join(folder,"rendered.json")));
  assert(record.atlas_pdf_sha256===sha(fs.readFileSync(path.join(directory,"digital-ip-atlas.pdf")))
         && JSON.stringify(record.atlas_inputs)===JSON.stringify(bookInputs())
         && record.renderer_sha256===sha(fs.readFileSync(fileURLToPath(import.meta.url))),
         "published PDF or its inputs differ from the atlas manifest");
  const evidence=JSON.parse(read(path.join(folder,"digital-evidence.json")));
  // “当前效果”绑定这些数字输入。换版后可用纯整数检查重建证据，无须启动 EDA。
  for(const [name,value] of Object.entries(evidence.source_sha256)){
    const file=path.resolve(root,name);
    assert(file.startsWith(root+path.sep) && fs.existsSync(file)
           && sha(fs.readFileSync(file))===value,"digital evidence input changed: "+name);
  }
  for(const [name,value] of Object.entries(evidence.figures)){
    // Git 中发布 SVG 和预览，PDF 收进本地论文插图包。
    if(name.endsWith(".pdf"))continue;
    assert(fs.existsSync(path.join(folder,name)) && sha(fs.readFileSync(path.join(folder,name)))===value,
           "digital evidence figure mismatch: "+name);
  }
  const graphs=cards.flatMap(c=>c.graphs);
  assert(Object.keys(record.diagrams).length===graphs.length,"rendered manifest count differs");
  // 两种摘要分别约束编辑图源和公开文件，防止只改文字却沿用旧箭头。
  for (const g of graphs) {
    const data=record.diagrams[g.name], file=path.join(folder,g.name+".svg");
    assert(data?.source_sha256===g.hash && fs.existsSync(file),"stale or missing diagram: "+g.name);
    assert(data.svg_sha256===sha(fs.readFileSync(file)),"SVG bytes changed: "+g.name);
  }
  // 接口名、已决顺序和示例掩码直接写在手工矢量图上；对应的 RTL 或向量
  // 变化时让作者重读这些图。渲染程序本身不能从数字位图推测应画哪条状态边。
  for (const card of cards) {
    for (const g of card.graphs.filter(graph=>graph.native)) {
      const saved=record.diagrams[g.name];
      assert(saved.source_format==="svg" && JSON.stringify(saved.inputs)===JSON.stringify(nativeInputs(card)),
             "review native diagram against changed inputs: "+g.name);
    }
  }
}

function nativeInputs(card) {
  const refs=card.sourceRefs.filter(p=>p.startsWith("rtl/") || /^tb\/vec_/.test(p));
  return Object.fromEntries([...new Set(refs)].sort().map(p=>[p,sha(fs.readFileSync(path.join(root,p)))]));
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
        const data=await page.evaluate(async ({name,content,native})=>{
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
          const v=svg.viewBox.baseVal;
          const width=v.width,height=v.height;
          if (!(width>0&&height>0)||svg.querySelector(".error-icon")) throw new Error("invalid SVG "+name);
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
          width:data.width,height:data.height,print_scale:scale};
        if(graph.native) signed.diagrams[graph.name].inputs=nativeInputs(card);
        const heading=graph.native ? "" : '<figcaption>'+labels[graph.kind]+'</figcaption>';
        illustrations.push('<figure'+(graph.native?' class="schematic"':'')+'>'+heading+data.svg+'</figure>');
      }
      const addr=card.chapter+'-'+card.anchor;
      const ref=[...new Set(card.sourceRefs)].filter(s=>!s.startsWith("docs/algorithms/figures/"))
        .map(escapeHtml).join(' · ');
      assembled.push('<article id="'+addr+'"><h2>'+escapeHtml(card.title)+'</h2><p><b>优势　</b>'+
        escapeHtml(card.benefit)+'</p><p><b>效果　</b>'+escapeHtml(card.effect)+'</p>'+
        card.notes.map(n=>'<p>'+escapeHtml(n)+'</p>').join('')+
        '<p class="refs">实现与核对　'+ref+'</p>'+illustrations.join('')+'</article>');
      navigation.push('<a href="#'+addr+'">'+escapeHtml(card.title)+'</a>');
    }
    const css='*{box-sizing:border-box}body{margin:0;color:#193645;font:15px/1.55 Arial,"PingFang SC",sans-serif}main{max-width:1000px;padding:22px 36px;margin:auto}h1,h2{line-height:1.23}article{padding:18px 0 28px;border-top:1px solid #d0dbdf}figure{margin:16px 0;text-align:center}figcaption{color:#59727c;font-size:12px}figure svg{display:block;margin:5px auto;max-width:100%;height:auto;max-height:510px}nav{columns:2;margin-bottom:18px}nav a{display:block;text-decoration:none;color:#166579}p{margin:6px 0}.refs{font-size:11px;color:#60747c;overflow-wrap:anywhere}@page{size:A4;margin:12mm 14mm}@media print{main{padding:0}article{break-before:page;border:0;padding:0}nav{font-size:8pt;columns:3}figure{break-inside:avoid;margin:5pt 0}figure svg{max-height:83mm}.refs{font-size:7pt}body{font-size:10pt}p{margin:3pt 0}}';
    // 离线图册内联已渲染的 SVG。阅读者不必联网加载第三方脚本或字库。
    // 定量图与协议图证据性质不同，因此在算法卡片之前按标题展示各自口径。
    // 以独立 SVG 图片内联，防止多个导出图的 clipPath 或字体 ID 相互覆盖。
    const evidenceFolder=path.join(directory,"figures");
    const evidence=JSON.parse(read(path.join(evidenceFolder,"digital-evidence.json")));
    // 定向期望值、数学复算和寄存器推演分别在图注中给出证据类型。
    // 每页只对应一个文件摘要，不能把另一批输入的相同图名拼入本册。
    // 前两页首先回答两个可观测的问题：等权位置由谁承担，20 位输出误差改变多少。
    // 第一页逐格累加归档叶子输出，每次选 35 个位置是单条命令的量。
    // 第二页以相同理想输出为零点。表中两个符号只在同一行的 raw 上比较。
    // 后面的调度统计和三沿交接示意各自保留观察单位，不能拼接成同一次硬件采集。
    const plates=[
      ["physical-masks","相同粗码如何轮换等权位置","归档叶子开关输出。固定同一相位的 64 次观察只用到 35 个等权位置。64 种相位各用一次，所有 64 个位置就各工作 35 次。每次转换的名义命令都是 283。"],
      ["integer-arithmetic","已知的单元偏差能校正几码","参考输出为 567,040 码。按定点合同将一处已知偏差注入 ADC2 原码，每行用同一原码比较默认表和校正表。只有一侧选中时，默认表多或少 32 码，写入已知权重后两者都回到参考输出。开关来自归档叶子，输出由当前数字参考计算。"],
      ["slice-allocation","片选公平性","20,000 次有效推进，三种片组角色按实际物理片号绘制。"],
      ["sar-events","已决命令序列","测试台定向输入的整数复算，横轴为合法提交序号。"],
      ["capture-handover","同沿返回与捕获","三沿协议示例。响应取沿前登记的事务。"]
    ];
    const gallery=plates.map(([name,title,caption])=>{
      const file=name+".svg",bytes=fs.readFileSync(path.join(evidenceFolder,file));
      assert(sha(bytes)===evidence.figures[file],"unverified evidence plate: "+file);
      return '<article id="plate-'+name+'"><h2>'+title+'</h2><p>'+caption+'</p><img style="width:100%;height:auto" alt="'+title+'" src="data:image/svg+xml;base64,'+bytes.toString("base64")+'"></article>';
    });
    // 首页先给出数字 IP 与宏的接口总览；定量图读者可从同页目录直接进入。
    // 参考图的链接取自仓库图册导航，离线导出后仍能定位到厂家公开原图。
    const plateLinks=plates.map(([name,title])=>'<a href="#plate-'+name+'">'+escapeHtml(title)+'</a>');
    const intro=read(path.join(directory,"README.md"));
    const references=[...intro.matchAll(/\[([^\]\n]*(?:原图源码|状态机原图))\]\((https:\/\/[^)]+)\)/g)];
    const links=references.map(([,label,url])=>'<a href="'+escapeHtml(url)+'">'+escapeHtml(label)+'</a>');
    const printRules='figure.schematic svg{width:100%;max-height:none}.design-refs a{color:#006d6a}'+
      '@media print{#digital-transaction{break-before:auto;padding-top:6pt}.refs{break-after:avoid}'+
      'figure.schematic{margin:6pt 0}#digital-events figure:not(.schematic) svg{max-height:65mm}}';
    const html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>20 位 SAR ADC 数字 IP · 算法与状态机</title><style>'+
       css+printRules+'</style><main><h1>20 位 SAR ADC 数字 IP · 算法与状态机</h1><nav>'+
       navigation.concat(plateLinks).join('')+'</nav>'+
       (links.length?'<p class="design-refs">绘图参考　'+links.join('　')+'</p>':'')+
       assembled[0]+gallery.join('')+assembled.slice(1).join('')+'</main></html>';
    fs.writeFileSync(path.join(output,"atlas.html"),html);
    await print.setContent(html);await print.evaluate(()=>document.fonts.ready);
    await print.pdf({path:path.join(output,"atlas.pdf"),preferCSSPageSize:true,printBackground:true});
    signed.atlas_inputs=bookInputs();
    signed.atlas_pdf_sha256=sha(fs.readFileSync(path.join(output,"atlas.pdf")));
    fs.writeFileSync(path.join(output,"svg","rendered.json"),JSON.stringify(signed,null,2)+"\n");
  } finally {await browser.close();}
}

const {cards,sourceCount}=loadCards();
if (argv.includes("--render")) await render(cards);
else if (argv.length===0 || (argv.length===1 && argv[0]==="--check")) checkAssets(cards);
else throw new Error("use --check or --render --out DIR --mermaid-js JS [--playwright MODULE] [--browser EXE]");
const graphs=cards.flatMap(c=>c.graphs);
console.log(cards.length+" algorithms, "+graphs.filter(g=>g.kind==="flow").length+" flows, "+
  graphs.filter(g=>g.kind==="state").length+" state diagrams, "+sourceCount+" production RTL sources");
