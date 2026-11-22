#!/usr/bin/env node
// =============================================================================
// 文件名     : check_manual_trace.mjs
// 模块名     : AD4080 手册逐模块追溯覆盖核查
// 版本       : v1.0
// 功能       : 从当前 checkout 收集模块与校准入口，逐行核对 MANUAL_TRACE.md。
// 设计说明   : 生产清单、DSP 原目录和显式接口给出必审范围。全外围再扫描一遍，
//              由唯一验证专用清单承担测试台和验证模型的余集。
//              普通校验建立引用和覆盖结构，强匹配模式进一步读报告中的器件裁定。
// 用法       : 安装到 verify/ 后可直接运行。留在备份目录时用 --root 指向外围目录。
// 变更记录   : 初始版本，暂无变更记录。
// =============================================================================

import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, realpathSync, statSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

const LISTS = ['integration/files.f', 'integration/readback_files.f', 'integration/product/rtl.f'];
// 两个评审接口代表独立的闭合边界，在主文件表之外也必须分别审阅。
const REVIEWS = ['interfaces/control_review/ad4080_csr_power_bridge.sv',
  'interfaces/dsp_review/ad4080_record_guard.sv'];
const CAL = ['system_cal.py', 'characterize_registers.py', 'verify_rtl.py'];
// 状态是手册可支持的强度，和本项目仿真 PASS 属两个判断轴。
const DEVICE_STATES = ['可认可', '部分认可', '不认可', '工程自定'];
const ADI_PDF = '/media/en/technical-documentation/data-sheets/ad4080.pdf';
// URL 会随供应商修订而更换内容。题注解析只认已核验的固定版二进制身份。
const ADI_REV_B_SHA256 = 'f08f2586dc82d5f583b42071b3570c06f1fe736d3053e15a559a47ed2841cbc1';
const LINK_TARGET = '(<[^>]+>|[^\\s)]+)';
const SOURCE_CELL = new RegExp('^`([^`]+)`\\s+\\[源码\\]\\(' + LINK_TARGET + '\\)$');
const INLINE_LINK = /\[((?:`[^`]*`|\[[^\]\n]*\]|[^\[\]\n])+)\]\((<[^>\n]+>|[^\s)]+)\)/g;

// =============================================================================
// 1. 读当前 checkout 的原始声明
// =============================================================================
const read = (file) => readFileSync(file, 'utf8');
const lines = (text) => text.split(/\r?\n/);
const mask = (text) => text.replace(/[^\r\n]/g, ' ');

function within(root, path) {
  // 通过真正的路径分量比较，同名前缀的相邻 checkout 不能混入来源。
  const rel = relative(root, path);
  return !isAbsolute(rel) && rel !== '..' && !rel.startsWith(`..${sep}`);
}

function sourceFile(path, scope) {
  // 先解析符号链接，再判定边界。工作目录里指向外面的重复文件不能替代本树证据。
  const target = realpathSync(path);
  if (!within(scope, target) || !statSync(target).isFile()) {
    throw new Error(`源码不在规定的实际目录中或不是文件 ${path}`);
  }
  return target;
}

function collectFilelist(root, dsp, file, cwd, result, visiting) {
  // 用当前递归路径检测环。被两个独立入口共同引用的子表仍可分别解析和去重。
  const path = sourceFile(file, root);
  if (visiting.has(path)) throw new Error(`filelist 循环引用 ${relative(root, path)}`);
  visiting.add(path);
  for (const [index, line] of lines(read(path)).entries()) {
    // 当前表的工作目录是外围根。支持嵌套表，并保留 -F 的相对路径规则。
    const text = line.replace(/(?:^|\s)(?:\/\/|#).*$/, '').trim();
    if (!text) continue;
    const entries = text.match(/"[^"]+"|'[^']+'|\S+/g).map((word) => word.replace(/^("|')(.*)\1$/, '$2'));
    for (let i = 0; i < entries.length; i++) {
      const word = entries[i];
      if (word === '-f' || word === '-F') {
        const target = entries[++i];
        if (!target) throw new Error(`${relative(root, path)}:${index + 1} 缺子 filelist`);
        const child = resolve(cwd, target);
        collectFilelist(root, dsp, child, word === '-F' ? dirname(child) : cwd, result, visiting);
      } else if (word.endsWith('.sv')) {
        // 生产构建复制到 bound_dsp 的同名文件，在审计时取本 checkout 的原始 DSP 目录。
        const staged = word.replace(/^\.\//, '');
        const bound = staged.startsWith('bound_dsp/');
        const name = bound ? staged.slice('bound_dsp/'.length) : word;
        result.add(sourceFile(resolve(bound ? dsp : cwd, name), bound ? dsp : root));
      } else if (!/^\+(?:incdir|define)\+/.test(word)) {
        // 无法确认会不会隐式加入 RTL 的新工具选项须被发现，避免缩小实际审计范围。
        throw new Error(`${relative(root, path)}:${index + 1} 不支持的清单条目 ${word}`);
      }
    }
  }
  visiting.delete(path);
}

function svFiles(dir, scope, recursive) {
  // 全外围收齐原始 SV。目录符号链接不展开，以免重复统计合成运行目录或产生循环。
  const result = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const target = resolve(dir, entry.name);
    if (entry.isDirectory() && recursive && entry.name !== '.git') result.push(...svFiles(target, scope, true));
    else if (entry.name.endsWith('.sv') && (entry.isFile() || entry.isSymbolicLink())) result.push(sourceFile(target, scope));
  }
  return result;
}

function declarations(files) {
  const all = new Map();
  for (const path of new Set(files)) {
    // 掩去注释、字符串和预处理行。保留分行，便于缺失项报告精确声明位置。
    const clean = read(path).replace(/\/\*[\s\S]*?\*\/|\/\/[^\r\n]*|"(?:\\[\s\S]|[^"\\])*"/g, mask)
      .replace(/^[ \t]*`[A-Za-z_]\w*(?:[^\r\n]*\\\r?\n)*[^\r\n]*/gm, mask);
    // 查生效条件之外的原始字面声明，条件编译的同名形态由同一条追溯承担。
    for (const match of clean.matchAll(/(?<![\w$\\])(?:module|macromodule)(?![\w$])(?:\s+(?:automatic|static))?\s+(\\\S+|[A-Za-z_$][\w$]*)/g)) {
      const name = match[1];
      const line = lines(clean.slice(0, match.index)).length;
      if (!all.has(name)) all.set(name, []);
      // 同名视为一项，但保留各原始声明位置。一个文件的不同名字仍需分别追溯。
      all.get(name).push({ path, line });
    }
  }
  return all;
}

function collect(root) {
  // DSP 用相邻源码而非临时 bound_dsp 的副本，新增 DSP 叶子即使未接线也进入清单。
  const dsp = realpathSync(resolve(root, '../ad4080_dsp'));
  const designFiles = new Set(svFiles(dsp, dsp, false));
  for (const name of [...REVIEWS]) designFiles.add(sourceFile(resolve(root, name), root));
  for (const name of LISTS) collectFilelist(root, dsp, resolve(root, name), root, designFiles, new Set());
  const hardware = declarations(designFiles);
  const verification = declarations(svFiles(root, root, true));
  // 生产集合以符号为键作差，因此共享源或同名声明不会变成额外的验证义务。
  for (const name of hardware.keys()) verification.delete(name);
  // Python 按用户指定的入口文件记账。模块符号必须带 .py，避免和 HDL 同名混淆。
  const scripts = new Map(CAL.map((name) => [name, [{ path: sourceFile(resolve(root, 'calibration', name), root) }]]));
  return { dsp, hardware, scripts, verification, required: new Map([...hardware, ...scripts]) };
}

// =============================================================================
// 2. 文件、图表定位和 Markdown 对应行
// =============================================================================
function activeLines(markdown) {
  const result = [];
  let fence = null;
  // 注释和代码示例无法为可见追溯表提供覆盖，保留原行号后排除。
  for (const [index, line] of lines(markdown.replace(/<!--[\s\S]*?-->/g, mask)).entries()) {
    const mark = /^\s*(`{3,}|~{3,})(.*)$/.exec(line);
    // 结束栅栏使用同一字符且不少于开启长度，文档中的嵌套短栅栏仍算示例内容。
    if (mark && !fence) fence = mark[1];
    else if (mark && fence && mark[1][0] === fence[0] && mark[1].length >= fence.length && !mark[2].trim()) fence = null;
    else if (!fence) result.push([index + 1, line]);
  }
  return result;
}

function cells(row) {
  // 代码位表达式及转义竖线属于文字，不能被当作额外的空证据或状态列。
  const parts = row.split(/(`+[^`]*`+|\\\|)/g);
  const protectedParts = parts.map((part, i) => i % 2 ? part.replaceAll('|', '\u0000') : part);
  return protectedParts.join('').split('|').slice(1, -1).map((part) => part.replaceAll('\u0000', '|').trim());
}

function links(text) {
  // 地址字段可带方括号，例如 [寄存器 0x15[4]](URL)。一对内层括号不结束显示文本。
  return [...text.matchAll(INLINE_LINK)];
}

function unbracket(target) {
  return target.startsWith('<') ? target.slice(1, -1) : target;
}

function manufacturerPage(target) {
  let url;
  try { url = new URL(unbracket(target)); } catch { return null; }
  // 只接受该产品的 ADI 手册端点。通用官网文章和第三方同名 PDF 无法替代页锚点。
  if (url.protocol !== 'https:' || url.port || url.username || url.password ||
      !['analog.com', 'www.analog.com'].includes(url.hostname) || url.pathname.toLowerCase() !== ADI_PDF) return null;
  // null 是其它来源，0 是本手册链接但页锚点有误，二者的失败含义不同。
  const page = /^#page=(\d{1,2})$/.exec(url.hash);
  return page && Number(page[1]) >= 1 && Number(page[1]) <= 95 ? Number(page[1]) : 0;
}

function manualCaptions(filename) {
  const pdf = readFileSync(filename);
  // 给提取器送已验哈希的原字节，确保计算身份与版面识别读取完全相同的输入。
  const digest = createHash('sha256').update(pdf).digest('hex');
  if (digest !== ADI_REV_B_SHA256) throw new Error(`AD4080 Rev. B PDF SHA-256 不符，实际 ${digest}`);
  const extractor = String.raw`
import json, re, sys
import fitz
with fitz.open(stream=sys.stdin.buffer.read(), filetype="pdf") as doc:
    if len(doc) != 95:
        raise SystemExit(f"Rev. B PDF 应有 95 页，实际 {len(doc)}")
    pages = []
    for number, page in enumerate(doc, 1):
        # 封面按文件页 1。其余页使用 Rev. B 版心中的印刷页号确认对应关系。
        if number > 1 and not re.search(rf"Rev\.\s*B\s*\|\s*{number}\s+of\s+95\b", page.get_text()):
            raise SystemExit(f"Rev. B 印刷页码不符，文件页 {number}")
        captions = []
        # 版面字号与字体来自文字行。关掉位图提取，省去和题注核验无关的图像拷贝。
        flags = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES
        for block in page.get_text("dict", flags=flags)["blocks"]:
            for line in block.get("lines", []):
                spans = line["spans"]
                # 此固定版本的题注使用粗斜体。正文可能恰从 Figure N. 开始，字体可区分二者。
                if not spans or spans[0]["font"] != "Arial-BoldItalicMT":
                    continue
                label = "".join(span["text"] for span in spans).strip()
                # 先合并同一行的字形 span，避免下标和公式造成一行题注被拆成数段。
                match = re.match(r"(Figure|Table)\s+(\d+)\.\s*", label)
                if match:
                    captions.append((match[1], int(match[2])))
        pages.append(captions)
print(json.dumps(pages))
`;
  // 可选 PDF 模式才启动 Python。默认模式不依赖 PDF 库或外部程序。
  const result = spawnSync('python3', ['-B', '-c', extractor], { input: pdf, encoding: 'utf8' });
  // 环境缺失或提取失败与错 PDF 同为核验中断，不能退回只看 Markdown 格式。
  if (result.status !== 0) throw new Error(`PDF 题注提取需要 Python3 与 PyMuPDF：${result.error?.message ?? result.stderr.trim()}`);
  const captions = new Map();
  for (const [index, page] of JSON.parse(result.stdout).entries()) {
    for (const [kind, ordinal] of page) {
      const key = (kind === 'Figure' ? '图' : '表') + ordinal;
      // 续表会重复题注，例如表 2 在多个印刷页。保留各页供正向和错误诊断共用。
      if (!captions.has(key)) captions.set(key, []);
      captions.get(key).push(index + 1);
    }
  }
  return captions;
}

function labelCaptions(label) {
  // 只解析链接可见的题注编号，前面的 p.xx、后面的寄存器地址均与图表号无关。
  const visible = label.normalize('NFKC').replace(/[`*]/g, '');
  // 编号组在下一处图/表类型词之前结束，混合标签仍各用自己的编号域。
  const groups = /(图|表|\bfig(?:ures?|s)?\.?|\btables?)\s*(\d+(?:\s*(?:[-–—~〜～至、,，/]|和|及|与|\band\b)\s*\d+)*)/gi;
  const results = [];
  for (const [, title, numbers] of visible.matchAll(groups)) {
    const kind = title === '表' || title.toLowerCase().startsWith('table') ? '表' : '图';
    for (const item of numbers.split(/\s*(?:[、,，/]|和|及|与|\band\b)\s*/i)) {
      const range = /^(\d+)(?:\s*[-–—~〜～至]\s*(\d+))?$/.exec(item);
      const first = Number(range?.[1]), last = Number(range?.[2] ?? range?.[1]);
      // 该 95 页手册的编号皆为正整数，最多三位。截断超长输入会掩盖错引用。
      if (!(1 <= first && first <= last && last <= 999)) throw new Error(`无效${kind}编号或区间 ${item}`);
      for (let n = first; n <= last; n++) results.push(kind + n);
    }
  }
  return results;
}

// 题注检查使用原 PDF 布局位置，正文引述的同名图号不能冒充题注。
// 页码保留手册印刷坐标，解析结果与 Git 中的中文审阅分开。
function auditPdf(documentLines, captions, errors) {
  // 一条图表在多个模块中引用很常见。报告按页、种类、编号去重，错误仍给出逐行来源。
  const unique = new Set();
  for (const [index, line] of documentLines) {
    // 总述及后续决策中的手册链接同样要查；栅栏示例、注释已由 activeLines 排除。
    for (const [, label, target] of links(line)) {
      const page = manufacturerPage(target);
      if (page === null) continue;
      const location = `MANUAL_TRACE.md:${index} [${label}]`;
      try {
        const expected = labelCaptions(label);
        // 总述的原件链接可以指向整份已验 PDF。命名图表或显式给页锚点时则须可定位。
        if (!page && (expected.length || new URL(unbracket(target)).hash)) {
          errors.push(`${location} 需要有效 AD4080 手册 #page=1..95`);
          continue;
        }
        for (const key of expected) {
          unique.add(`${page}/${key}`);
          const places = captions.get(key) ?? [];
          if (!places.includes(page)) errors.push(`${location} p.${page} 无${key}题注，实际页 ${places.join('、') || '无'}`);
        }
      } catch (error) { errors.push(`${location} ${error.message}`); }
    }
  }
  console.log(`AD4080 Rev. B PDF SHA-256 已核 · 95 页 · ${unique.size} 个唯一页图表引用`);
}

function hasLocator(cellsToCheck) {
  // 位置词只在实际呈现的文字里找，URL 或裸页码无法冒充具体图表、章节或寄存器字段。
  const visible = cellsToCheck.join(' ').replace(INLINE_LINK, '$1');
  return /(?:图|表|figure|fig\.|table)\s*\d+/i.test(visible) || /\d+(?:\.\d+)*\s*[章节]/.test(visible) ||
    /(?:章节|小节|section|字段|寄存器|register)\s*(?:`[^`]+`|[“「][^”」]+[”」]|[：:]\s*[^\s，。]+|0x[\da-f]+|[A-Za-z][\w-]+)/iu.test(visible) ||
    /0x[\da-f]+\s*\[\d+(?::\d+)?\]/i.test(visible) || /`[A-Z][A-Z0-9]*_[A-Z0-9_]+`/.test(visible);
}

function checkedLine(path, fragment) {
  if (!fragment || !fragment.startsWith('L')) return;
  const match = /^L([1-9]\d*)(?:-L?([1-9]\d*))?$/.exec(fragment);
  // 结尾换行不增加可引用的空行，消除常见的最后一行差一误差。
  const total = lines(read(path).replace(/\r?\n$/, '')).length;
  if (!match || Number(match[1]) > Number(match[2] ?? match[1]) || Number(match[2] ?? match[1]) > total) {
    throw new Error(`无效行锚点 ${path}#${fragment}（${total} 行）`);
  }
}

function checkedLocal(target, manual, checkout) {
  // Markdown 路径从手册所在目录解释。标准 %20 写法及尖括号内空格均可用。
  const [file, fragment] = unbracket(target).split('#', 2);
  const decoded = decodeURIComponent(file);
  if (isAbsolute(decoded)) throw new Error(`本地链接须使用相对路径 ${target}`);
  const path = realpathSync(file ? resolve(dirname(manual), decoded) : manual);
  if (!within(checkout, path)) throw new Error(`链接离开当前 checkout ${target}`);
  checkedLine(path, fragment);
  return path;
}

// 固定 DSP 源码锚点必须指向本次冻结提交。
// 本地链接需要位于可交付树，防止报告寄希望于构建临时目录。
function checkedSource(target, manual, checkout, dsp, dspBlob) {
  const raw = unbracket(target);
  if (!/^[A-Za-z][\w+.-]*:/.test(raw)) return checkedLocal(raw, manual, checkout);
  const url = new URL(raw);
  // 原 DSP 的可点击发布链接固定在审过的 commit。这里只验证对应本地声明，无网络读取。
  if (url.origin !== 'https://github.com' || url.username || url.password || url.search ||
      !url.pathname.startsWith(dspBlob)) throw new Error(`源码链接须为本地或固定的原 DSP GitHub blob ${target}`);
  const file = sourceFile(resolve(dsp, decodeURIComponent(url.pathname.slice(dspBlob.length))), dsp);
  checkedLine(file, url.hash.slice(1));
  return file;
}

function audit(root, inventory, checkLocal, captions) {
  const manual = resolve(root, 'evidence/MANUAL_TRACE.md');
  const revision = read(resolve(root, 'evidence/dsp-revision.txt')).trim();
  if (!/^[0-9a-f]{40}$/.test(revision)) throw new Error('DSP 来源必须为完整的 Git 提交号');
  const dspBlob = '/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/' + revision + '/rtl/ad4080_dsp/';
  const checkout = dirname(root).endsWith(`${sep}rtl`) ? resolve(root, '../..') : dirname(root);
  const errors = [], seen = new Set(), validation = new Set();
  const counts = Object.fromEntries(DEVICE_STATES.map((state) => [state, 0]));
  let markers = 0;
  const documentLines = activeLines(read(manual));
  if (captions) auditPdf(documentLines, captions, errors);
  for (const [index, line] of documentLines) {
    const where = `MANUAL_TRACE.md:${index}`;
    const marker = /^\s*(?:[-*+]\s+)?(?:\*\*|__)?验证专用清单[：:](.*)$/.exec(line);
    if (marker) {
      markers++;
      // 验证例外集中在单行，逐名与全外围差集比对并显式拒绝重复和不存在的名字。
      for (const [, name] of marker[1].matchAll(/`([^`]+)`/g)) {
        if (validation.has(name)) errors.push(`${where} 重复验证专用 ${name}`);
        if (!inventory.verification.has(name)) errors.push(`${where} 未知或属于设计范围的验证项 ${name}`);
        validation.add(name);
      }
      continue;
    }
    // 普通说明表不参与追溯。源码条目或已知名字拼错格式时给出所在行。
    const row = line.trim();
    const probable = /^\|\s*`([^`]+)`/.exec(row);
    if (!/^ {0,3}\|/.test(line) || !(/\[源码\]/.test(row) || (probable && inventory.required.has(probable[1])))) continue;
    const values = row.endsWith('|') ? cells(row) : [];
    const source = SOURCE_CELL.exec(values[0] ?? '');
    if (values.length !== 4 || !source) {
      errors.push(`${where} 模块行须为 | \`符号\` [源码](链接) | 原厂定位 | 裁定 | 状态 |`);
      continue;
    }
    const [, name, target] = source;
    const [, evidence, judgement, status] = values;
    if (seen.has(name)) errors.push(`${where} 重复模块行 ${name}`);
    seen.add(name);
    const places = inventory.required.get(name);
    if (!places) errors.push(`${where} 未列入设计和校准范围 ${name}`);
    try {
      const path = checkedSource(target, manual, checkout, inventory.dsp, dspBlob);
      // 模块名与真实来源成对匹配。已有同名的其它工程文件不能替代当前声明。
      if (places && !places.some((place) => place.path === path)) errors.push(`${where} 源码文件与 ${name} 的声明不符`);
    } catch (error) { errors.push(`${where} ${name} ${error.message}`); }
    const refs = links(`${evidence} ${judgement}`);
    const pages = refs.map((ref) => manufacturerPage(ref[2])).filter((page) => page !== null);
    if (!pages.some(Boolean) || pages.includes(0)) errors.push(`${where} ${name} 需要有效 AD4080 手册 #page=1..95`);
    if (!evidence || !judgement) errors.push(`${where} ${name} 的依据和裁定均需填写`);
    if (Object.hasOwn(counts, status)) counts[status]++;
    else errors.push(`${where} ${name} 未知状态 ${status}`);
    if (['可认可', '部分认可'].includes(status) && !hasLocator([evidence, judgement])) {
      errors.push(`${where} ${name} 认可项需要具体图、表、章节或字段`);
    }
    if (checkLocal) {
      // 第一列始终查源码。其它交叉引用仅在本地资料齐备时按选项逐条检查。
      for (const ref of refs) {
        if (/^[A-Za-z][\w+.-]*:/.test(unbracket(ref[2]))) continue;
        try { checkedLocal(ref[2], manual, checkout); } catch (error) { errors.push(`${where} ${error.message}`); }
      }
    }
  }
  if (markers !== 1) errors.push(`须有唯一验证专用清单行，实际 ${markers}`);
  // 文件表变化和全目录中未入文件表的变化分别报明义务，避免仅比较固定总数。
  for (const name of inventory.required.keys()) if (!seen.has(name)) errors.push(`缺少设计/校准追溯 ${name}`);
  for (const name of inventory.verification.keys()) if (!validation.has(name)) errors.push(`未分类外围声明 ${name}，请加入设计清单或验证专用清单`);
  return { errors, counts };
}

// =============================================================================
// 3. 入口和可追溯的动态输出
// =============================================================================
function printInventory(root, inventory) {
  // 始终提供可找回的声明清单，失败时也可从当前 checkout 取待补行。
  for (const [label, group] of [['设计模块', inventory.hardware], ['校准脚本', inventory.scripts],
    ['验证专用', inventory.verification]]) {
    console.log(`${label} ${group.size}`);
    for (const [name, places] of [...group].sort(([a], [b]) => a.localeCompare(b))) {
      const locations = places.map(({ path, line }) => relative(root, path) + (line ? `:${line}` : ''));
      console.log(`  ${name}  ${locations.join(', ')}`);
    }
  }
}

function main() {
  const { values } = parseArgs({ options: { root: { type: 'string' }, inventory: { type: 'boolean' },
    'check-local': { type: 'boolean' }, 'require-device-match': { type: 'boolean' },
    'manual-pdf': { type: 'string' }, help: { type: 'boolean' } } });
  if (values.help) {
    console.log('node verify/check_manual_trace.mjs [--root 外围目录或checkout] [--inventory] [--check-local] [--manual-pdf AD4080.pdf] [--require-device-match]');
    console.log('正常模式校验 evidence/MANUAL_TRACE.md。--inventory 只读列声明。--check-local 检查依据中的本地链接。');
    console.log('--manual-pdf 核 Rev. B 哈希、95 页和链接标签的图表题注，需要本机 Python3 与 PyMuPDF。');
    console.log('退出 0 通过，1 结构或来源位置错误，2 在 --require-device-match 下仍存在部分认可或不认可。');
    return 0;
  }
  let selected = values.root ? resolve(values.root) : resolve(dirname(fileURLToPath(import.meta.url)), '..');
  // 在 checkout 根和子系统目录使用同一判据，支持待集成的 staging 布局。
  if (existsSync(resolve(selected, 'rtl/ad4080_periph', LISTS[0]))) {
    selected = resolve(selected, 'rtl/ad4080_periph');
  }
  const root = realpathSync(selected);
  const inventory = collect(root);
  printInventory(root, inventory);
  if (values.inventory) return 0;
  // --inventory 用于找出待填模块。正常检验才需要手册与 PDF 原件。
  const captions = values['manual-pdf'] === undefined ? null : manualCaptions(values['manual-pdf']);
  const { errors, counts } = audit(root, inventory, values['check-local'], captions);
  console.log(DEVICE_STATES.map((state) => `${state} ${counts[state]}`).join(' · '));
  if (errors.length) {
    for (const error of errors) console.error(`FAIL ${error}`);
    return 1;
  }
  // 仅在引用结构通过后评估器件差异，退出码 2 表示有效报告仍有未闭合的器件项。
  if (values['require-device-match'] && counts['部分认可'] + counts['不认可']) {
    console.error(`DEVICE_MATCH_PENDING 部分认可 ${counts['部分认可']} · 不认可 ${counts['不认可']}`);
    return 2;
  }
  console.log('PASS 当前声明覆盖、原厂手册页锚点及裁定格式');
  return 0;
}

try { process.exitCode = main(); } catch (error) {
  console.error(`FAIL ${error.message}`);
  process.exitCode = 1;
}
