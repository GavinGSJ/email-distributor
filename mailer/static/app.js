"use strict";

const $ = (id) => document.getElementById(id);
const EMAIL_COL = "邮箱";
const EMAIL_HEADERS = ["email", "e-mail", "mail", "邮箱", "邮件", "电子邮箱", "电子邮件", "收件人邮箱"];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtSize = (n) => (n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB");

let S = {};           // server state: account, signature, templates, lists, providers
let C = null;         // current compose
let lastField = null; // subject/body field that last had focus, for variable insertion

const blankCompose = (cc = "") => ({
  subject: "", body: "", cc, bcc: "",
  columns: [EMAIL_COL, "公司", "联系人"], rows: [{}, {}, {}],
  variables: [{ name: "项目名称", value: "" }, { name: "项目代号", value: "" }, { name: "采购物资", value: "" }],
  attachments: [], use_signature: true,
});
const GONE = "邮件助手已关闭，请重新双击“邮件助手”打开";

// ---------------------------------------------------------------- api / ui helpers
async function api(path, body, rawName) {
  const opts = { method: body === undefined ? "GET" : "POST", headers: { "X-Token": window.TOKEN } };
  if (rawName !== undefined) { opts.body = body; path += "?name=" + encodeURIComponent(rawName); }
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  let res;
  try { res = await fetch(path, opts); } catch { throw new Error(GONE); }
  const data = await res.json().catch(() => ({ error: GONE }));
  if (!res.ok) { const e = new Error(data.error || "出错了"); e.problems = data.problems; throw e; }
  return data;
}

function toast(msg, isError) {
  const t = $("toast");
  t.textContent = msg; t.className = "toast" + (isError ? " error" : "");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.add("hidden"), isError ? 5000 : 2500);
}

function showError(e) {
  if (e.problems) {
    modal(`<h3>${esc(e.message)}</h3><ul class="problems">${e.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>`,
      [["知道了", null, true]]);
  } else toast(e.message, true);
}

// buttons: [label, onClick, primary]; onClick returning false keeps the modal open
function modal(html, buttons) {
  $("modal-body").innerHTML = html;
  const box = $("modal-actions"); box.innerHTML = "";
  for (const [label, fn, primary] of buttons) {
    const b = document.createElement("button");
    b.textContent = label; if (primary) b.className = "primary";
    b.onclick = async () => { if (fn && (await fn(b)) === false) return; closeModal(); };
    box.appendChild(b);
  }
  $("modal").classList.remove("hidden");
}
const closeModal = () => $("modal")?.classList.add("hidden");

function ask(title, value = "") {
  return new Promise((resolve) => {
    modal(`<h3>${esc(title)}</h3><input id="ask-input" type="text" value="${esc(value)}">`,
      [["取消", () => resolve(null)], ["确定", () => resolve($("ask-input").value.trim()), true]]);
    setTimeout(() => $("ask-input").focus(), 0);
  });
}

async function busy(btn, fn) {
  const old = btn.textContent; btn.disabled = true; btn.textContent = "处理中…";
  try { return await fn(); } finally { btn.disabled = false; btn.textContent = old; }
}

function pickFiles(input, multiple) {
  return new Promise((resolve) => {
    input.value = ""; input.multiple = !!multiple;
    input.onchange = () => resolve([...input.files]);
    input.click();
  });
}

// ---------------------------------------------------------------- navigation
function go(view) {
  if (!S.account && view !== "account") view = "account";
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("hidden", v.id !== "view-" + view));
  document.querySelectorAll(".nav").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === view);
    b.disabled = !S.account && b.dataset.view !== "account";
  });
  $("bar").classList.toggle("hidden", view !== "compose");
  window.scrollTo(0, 0);
  if (view === "account") fillAccount();
  if (view === "signature") fillSignature();
  if (view === "library") { LIB.open = null; renderLibrary(); }
}

// ---------------------------------------------------------------- account
function providerFor(email) {
  const domain = (email.split("@")[1] || "").toLowerCase().trim();
  return { domain, known: S.providers.personal[domain] || null };
}

function applyProvider(p) {
  $("acc-host").value = p.host; $("acc-port").value = p.port; $("acc-sec").value = p.security;
  $("acc-imap-host").value = p.imap_host || ""; $("acc-imap-port").value = p.imap_port || 993;
  $("acc-imap-sec").value = p.imap_security || "ssl";
  $("acc-help").textContent = p.help ? "如何获取：" + p.help : "";
}

function onEmailChange(fromUser) {
  const { domain, known } = providerFor($("acc-email").value);
  $("acc-ent-wrap").classList.toggle("hidden", !domain || !!known);
  if (known) {
    $("acc-provider").textContent = `✓ 已识别：${known.name}（${known.host}）`;
    if (fromUser) applyProvider(known);
  } else if (domain) {
    $("acc-provider").textContent = "这是公司邮箱，请选择邮箱服务商（不确定可问 IT 同事）";
    if (fromUser) onEntChange();
  } else $("acc-provider").textContent = "";
}

function onEntChange() {
  const i = $("acc-ent").value;
  if (i === "other") {
    const { domain } = providerFor($("acc-email").value);
    applyProvider({ host: "smtp." + domain, port: 465, security: "ssl", imap_host: "imap." + domain, imap_port: 993, imap_security: "ssl",
      help: "请向公司 IT 确认 SMTP/IMAP 服务器地址、端口和加密方式" });
    $("acc-adv").open = true;
  } else applyProvider(S.providers.enterprise[i]);
}

function fillAccount() {
  const a = S.account;
  $("acc-ent").innerHTML = S.providers.enterprise.map((p, i) => `<option value="${i}">${esc(p.name)}</option>`).join("")
    + '<option value="other">其他（手动填写服务器）</option>';
  $("acc-title").textContent = a ? "账号设置" : "第一步：登录发件邮箱";
  $("acc-intro").textContent = a ? "修改后点击“测试并保存”。密码留空表示不修改。"
    : "只需设置一次，之后自动保存。密码会加密保存在本机，只有当前 Windows 用户能读取。";
  $("acc-email").value = a?.email || "";
  $("acc-name").value = a?.name || "";
  $("acc-password").value = "";
  $("acc-password").placeholder = a?.has_password ? "已保存（不修改请留空）" : "多数邮箱需填“授权码”，不是登录密码";
  if (a) {
    $("acc-host").value = a.host; $("acc-port").value = a.port; $("acc-sec").value = a.security;
    $("acc-imap-host").value = a.imap_host || ""; $("acc-imap-port").value = a.imap_port || 993;
    $("acc-imap-sec").value = a.imap_security || "ssl";
    $("acc-save-sent").checked = !!a.save_sent;
    const ent = S.providers.enterprise.findIndex((p) => p.host === a.host);
    $("acc-ent").value = ent >= 0 ? ent : "other";
    onEmailChange(false);
    const known = providerFor(a.email).known || S.providers.enterprise[ent];
    $("acc-help").textContent = known ? "如何获取：" + known.help : "";
  } else { $("acc-save-sent").checked = true; onEmailChange(true); }
}

async function saveAccount() {
  const req = {
    email: $("acc-email").value.trim(), name: $("acc-name").value.trim(), password: $("acc-password").value,
    host: $("acc-host").value.trim(), port: $("acc-port").value, security: $("acc-sec").value,
    save_sent: $("acc-save-sent").checked, imap_host: $("acc-imap-host").value.trim(),
    imap_port: $("acc-imap-port").value, imap_security: $("acc-imap-sec").value,
  };
  const save = async (skipTest) => {
    S.account = await api("/api/account", { ...req, skip_test: skipTest });
    renderWho();
    toast(skipTest ? "已保存（未验证登录）"
      : S.account.sent_folder ? `登录成功，已保存。发出的邮件将存入“${S.account.sent_folder}”` : "登录成功，已保存");
    go("compose");
  };
  await busy($("acc-save"), async () => {
    try { await save(false); }
    catch (e) {
      // A network problem shouldn't stop the user from saving; a wrong password should be fixed now.
      if (!e.message.includes("无法连接")) return showError(e);
      modal(`<h3>暂时连不上邮件服务器</h3><p class="problems">${esc(e.message)}</p>
        ${e.problems ? `<p class="muted small">${e.problems.map(esc).join("<br>")}</p>` : ""}
        <p>可以先保存设置；发送时程序会先登录，登录不成功不会发出任何邮件。</p>`,
        [["返回修改"], ["仍然保存", async () => { try { await save(true); } catch (err) { showError(err); } }, true]]);
    }
  });
}

function renderWho() {
  const a = S.account;
  $("who").textContent = a ? `${a.name ? a.name + " · " : ""}${a.email}` : "";
}

// ---------------------------------------------------------------- signature
let sigLogo = null;

function fillSignature() {
  $("sig-text").value = S.signature.text || "";
  sigLogo = S.signature.logo || null;
  renderSigPreview();
}

function renderSigPreview() {
  $("sig-logo-del").classList.toggle("hidden", !sigLogo);
  $("sig-logo").innerHTML = sigLogo ? `<span class="muted small">${esc(sigLogo.name)}</span>` : '<span class="muted small">未设置</span>';
  const lines = $("sig-text").value.split("\n").map(esc).join("<br>");
  $("sig-preview").innerHTML = `<div style="color:#555;font-size:13px;line-height:1.6">${lines || '<span class="muted">（空）</span>'}</div>`
    + (sigLogo?.preview ? `<img src="${sigLogo.preview}" style="max-height:80px;margin-top:6px">` : "");
}

async function pickLogo() {
  const [file] = await pickFiles($("sig-logo-file"));
  if (!file) return;
  try {
    const info = await api("/api/upload", file, file.name);
    sigLogo = { ...info, preview: URL.createObjectURL(file) };
    renderSigPreview();
  } catch (e) { showError(e); }
}

async function saveSignature() {
  const logo = sigLogo ? { id: sigLogo.id, name: sigLogo.name } : null;
  await busy($("sig-save"), async () => {
    try {
      const res = await api("/api/signature", { text: $("sig-text").value, logo });
      S.signature = { text: $("sig-text").value, logo };
      $("sig-preview").innerHTML = res.html || '<span class="muted">（空）</span>';
      toast("签名已保存");
    } catch (e) { showError(e); }
  });
}

// ---------------------------------------------------------------- recipients table
function filledRows() {
  return C.rows.filter((r) => C.columns.some((c) => String(r[c] || "").trim()));
}

function renderTable() {
  const head = `<tr><th></th>${C.columns.map((c, i) => `<th>${esc(c)}${i ? ` <button class="x" data-delcol="${i}" title="删除此列">×</button>` : ' <span class="danger">*</span>'}</th>`).join("")}<th class="del"></th></tr>`;
  const body = C.rows.map((r, ri) => `<tr>
      <td class="idx">${ri + 1}</td>
      ${C.columns.map((c, ci) => `<td><input data-r="${ri}" data-c="${ci}" value="${esc(r[c] || "")}"${ci === 0 ? ' placeholder="name@company.com"' : ""}></td>`).join("")}
      <td class="del"><button class="x" data-delrow="${ri}" title="删除此行">×</button></td></tr>`).join("");
  $("rcpt-table").innerHTML = head + body;
  renderCount(); renderVars();
}

function renderCount() {
  const n = filledRows().length;
  $("rcpt-count").textContent = n ? `${n} 封` : "";
  $("btn-send").textContent = n ? `发送（${n} 封）` : "发送";
}

// ---------------------------------------------------------------- variables
const VAR_RE = /\{\{\s*([^{}\s]+)\s*\}\}/g;
const gvNames = () => C.variables.map((v) => v.name.trim()).filter(Boolean);

// [{name, kind}] for every variable usable in subject/body
function knownVars() {
  const out = [];
  for (const n of gvNames()) out.push({ name: n, kind: "通用" });
  for (const c of C.columns) if (!out.some((v) => v.name === c)) out.push({ name: c, kind: "收件人" });
  return out;
}

function renderVars() {
  const chips = gvNames().map((n) => `<span class="chip gv" data-var="${esc(n)}" title="通用变量">{{${esc(n)}}}</span>`)
    .concat(C.columns.filter((c) => c !== EMAIL_COL).map((c) => `<span class="chip" data-var="${esc(c)}" title="收件人表格的列">{{${esc(c)}}}</span>`));
  $("c-vars").innerHTML = chips.join("") || '<span class="muted small">（添加通用变量或给收件人表格加列后可用）</span>';
  renderWarnings();
}

function renderWarnings() {
  const used = new Set();
  for (const text of [$("c-subject").value, $("c-body").value]) for (const m of text.matchAll(VAR_RE)) used.add(m[1]);
  const known = new Set(knownVars().map((v) => v.name));
  const msgs = [];
  const unknown = [...used].filter((n) => !known.has(n));
  if (unknown.length) msgs.push(`还没有定义的变量：${unknown.map((n) => `{{${n}}}`).join("、")}（请添加为通用变量，或在收件人表格中加这一列）`);
  const empty = C.variables.filter((v) => used.has(v.name.trim()) && !String(v.value).trim()).map((v) => v.name.trim());
  if (empty.length) msgs.push(`通用变量还没填写内容：${empty.join("、")}`);
  const clash = gvNames().filter((n) => C.columns.includes(n));
  if (clash.length) msgs.push(`“${clash.join("、")}”既是通用变量又是收件人表格的列，请改名`);
  $("c-warn").innerHTML = msgs.map((m) => `⚠ ${esc(m)}`).join("<br>");
}

function renderCommonVars() {
  $("gv-list").innerHTML = C.variables.map((v, i) => `<div class="gv-row">
      <input data-gv="${i}" data-k="name" value="${esc(v.name)}" placeholder="变量名，如 项目名称">
      <input data-gv="${i}" data-k="value" value="${esc(v.value)}" placeholder="内容，如 XX 海上平台项目">
      <button class="x" data-delgv="${i}" title="删除">×</button></div>`).join("");
}

function onCommonVarInput(e) {
  const t = e.target; if (t.dataset.gv === undefined) return;
  C.variables[+t.dataset.gv][t.dataset.k] = t.value;
  if (t.dataset.k === "name") renderVars(); else renderWarnings();
  autosave();
}

// -- {{ autocomplete in subject/body
const AC = { field: null, items: [], idx: 0, start: 0 };

function caretPoint(f, pos) {
  const cs = getComputedStyle(f), r = f.getBoundingClientRect();
  const div = document.createElement("div");
  for (const p of ["fontFamily", "fontSize", "fontWeight", "lineHeight", "letterSpacing", "paddingTop", "paddingRight",
    "paddingBottom", "paddingLeft", "borderTopWidth", "borderLeftWidth", "boxSizing", "width"]) div.style[p] = cs[p];
  Object.assign(div.style, { position: "absolute", visibility: "hidden", top: "0", left: "-9999px",
    whiteSpace: f.tagName === "INPUT" ? "pre" : "pre-wrap", overflowWrap: "break-word" });
  div.textContent = f.value.slice(0, pos);
  const mark = document.createElement("span"); mark.textContent = "\u200b"; div.appendChild(mark);
  document.body.appendChild(div);
  const lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.6;
  const x = r.left + mark.offsetLeft - f.scrollLeft;
  const y = r.top + mark.offsetTop - f.scrollTop + lh;
  div.remove();
  return { x: Math.min(Math.max(x, r.left), r.right - 180), y: Math.min(y, r.bottom) + 2 };
}

function onFieldInput(e) {
  const f = e.target, pos = f.selectionStart;
  const m = f.value.slice(0, pos).match(/\{\{\s*([^{}\s]*)$/);
  const items = m ? knownVars().filter((v) => v.name.includes(m[1])) : [];
  if (!items.length) return hideAC();
  Object.assign(AC, { field: f, items, idx: 0, start: pos - m[0].length });
  const { x, y } = caretPoint(f, pos);
  const ac = $("ac");
  ac.style.left = x + "px"; ac.style.top = y + "px";
  renderAC(); ac.classList.remove("hidden");
}

function renderAC() {
  $("ac").innerHTML = AC.items.map((v, i) =>
    `<li data-ac="${i}" class="${i === AC.idx ? "active" : ""}">{{${esc(v.name)}}}<span class="kind">${v.kind}</span></li>`).join("");
}

const hideAC = () => { $("ac").classList.add("hidden"); AC.field = null; };

function chooseAC(i) {
  const f = AC.field, name = AC.items[i].name;
  const rest = f.value.slice(f.selectionStart).replace(/^[^{}\s]*\}\}/, ""); // swallow a half-typed tail
  f.value = f.value.slice(0, AC.start) + `{{${name}}}` + rest;
  f.selectionStart = f.selectionEnd = AC.start + name.length + 4;
  hideAC(); f.focus();
  f.dispatchEvent(new Event("input"));
}

function onFieldKey(e) {
  if (!AC.field) return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    AC.idx = (AC.idx + (e.key === "ArrowDown" ? 1 : -1) + AC.items.length) % AC.items.length; renderAC();
  } else if (e.key === "Enter" || e.key === "Tab") chooseAC(AC.idx);
  else if (e.key === "Escape") hideAC();
  else return;
  e.preventDefault();
}

function onCellInput(e) {
  const t = e.target; if (t.dataset.r === undefined) return;
  C.rows[+t.dataset.r][C.columns[+t.dataset.c]] = t.value;
  renderCount(); autosave();
}

// Paste a block copied from Excel (tab/newline separated) starting at the focused cell.
function onCellPaste(e) {
  const t = e.target; if (t.dataset.r === undefined) return;
  const text = e.clipboardData.getData("text");
  if (!/[\t\n]/.test(text.trim())) return;
  e.preventDefault();
  const lines = text.replace(/\r/g, "").replace(/\n+$/, "").split("\n");
  const r0 = +t.dataset.r, c0 = +t.dataset.c;
  lines.forEach((line, i) => {
    while (C.rows.length <= r0 + i) C.rows.push({});
    line.split("\t").forEach((cell, j) => {
      const col = C.columns[c0 + j];
      if (col) C.rows[r0 + i][col] = cell.trim();
    });
  });
  renderTable(); autosave();
  toast(`已粘贴 ${lines.length} 行`);
}

function onTableClick(e) {
  const t = e.target;
  if (t.dataset.delrow !== undefined) {
    C.rows.splice(+t.dataset.delrow, 1);
    if (!C.rows.length) C.rows.push({});
  } else if (t.dataset.delcol !== undefined) {
    const col = C.columns[+t.dataset.delcol];
    C.columns.splice(+t.dataset.delcol, 1);
    C.rows.forEach((r) => delete r[col]);
  } else return;
  renderTable(); autosave();
}

async function addColumn() {
  const name = await ask("新列的名称（可作为正文变量使用）", "");
  if (!name) return;
  if (/[{}\s]/.test(name)) return toast("列名不能包含空格或花括号", true);
  if (C.columns.includes(name)) return toast("已有同名列", true);
  if (gvNames().includes(name)) return toast(`“${name}”已经是通用变量了，请换个列名`, true);
  C.columns.push(name); renderTable(); autosave();
}

// Upload a CSV/Excel file and return {columns, rows} with the e-mail column renamed to 邮箱 and first.
async function importFile(file) {
  const data = await api("/api/import", file, file.name);
  const emailIdx = data.columns.findIndex((c) => EMAIL_HEADERS.includes(c.toLowerCase()));
  if (emailIdx < 0) throw new Error(`文件第一行需要有“邮箱”列。识别到的表头：${data.columns.join("、")}`);
  const src = data.columns[emailIdx];
  const columns = [EMAIL_COL, ...data.columns.filter((c, i) => i !== emailIdx && c.trim())];
  const rows = data.rows.map((r) => { const o = { ...r, [EMAIL_COL]: r[src] }; if (src !== EMAIL_COL) delete o[src]; return o; });
  return { columns, rows };
}

async function importTable() {
  const [file] = await pickFiles($("rcpt-file"));
  if (!file) return;
  try {
    const { columns: cols, rows } = await importFile(file);
    const replace = !filledRows().length || (await new Promise((res) =>
      modal(`<h3>导入 ${rows.length} 位收件人</h3><p>当前表格里已有内容，要怎么处理？</p>`,
        [["追加到末尾", () => res(false)], ["替换现有", () => res(true), true]])));
    if (replace) { C.columns = cols; C.rows = rows; }
    else {
      cols.forEach((c) => { if (!C.columns.includes(c)) C.columns.push(c); });
      C.rows = [...filledRows(), ...rows];
    }
    if (!C.rows.length) C.rows.push({});
    renderTable(); autosave(); toast(`已导入 ${rows.length} 行`);
  } catch (e) { showError(e); }
}

// ---------------------------------------------------------------- saved recipient lists
const emailKey = (r) => String(r[EMAIL_COL] || "").toLowerCase().split(/[;,；，\s]+/).filter(Boolean).sort().join(";");

async function saveList() {
  const rows = filledRows();
  if (!rows.length) return toast("表格里还没有收件人", true);
  const name = await ask(`把当前 ${rows.length} 位收件人保存为名单（同名会覆盖）`, "");
  if (!name) return;
  try { S.lists = await api("/api/lists", { name, list: { columns: C.columns, rows } }); toast(`名单“${name}”已保存`); }
  catch (e) { showError(e); }
}

function pickFromLists() {
  const names = Object.keys(S.lists).sort();
  if (!names.length) return toast("还没有保存的名单。先在表格里填好收件人，再点“保存为名单”", true);
  let current = names[0], picked = new Set();
  const existing = new Set(filledRows().map(emailKey));

  modal(`<h3>从名单添加收件人</h3>
    <div class="pick-head">
      <select id="pk-list">${names.map((n) => `<option>${esc(n)}</option>`).join("")}</select>
      <input id="pk-q" type="text" placeholder="搜索公司、联系人、邮箱…">
      <button id="pk-del" class="link danger">删除此名单</button>
    </div>
    <div class="pick-wrap"><table id="pk-table"></table></div>
    <p class="muted small">灰色行表示已在当前收件人表格中，不会重复添加。</p>`,
    [["取消"], ["添加选中", () => {
      const list = S.lists[current];
      const rows = [...picked].map((i) => list.rows[i]);
      if (!rows.length) { toast("请先勾选要添加的收件人", true); return false; }
      list.columns.forEach((c) => { if (!C.columns.includes(c)) C.columns.push(c); });
      C.rows = [...filledRows(), ...rows.map((r) => ({ ...r }))];
      renderTable(); autosave(); toast(`已添加 ${rows.length} 位收件人`);
    }, true]]);

  const render = () => {
    const list = S.lists[current];
    const q = $("pk-q").value.trim().toLowerCase();
    const visible = list.rows.map((r, i) => [r, i]).filter(([r]) => !q || list.columns.some((c) => String(r[c] || "").toLowerCase().includes(q)));
    const selectable = visible.filter(([r]) => !existing.has(emailKey(r)));
    const allOn = selectable.length && selectable.every(([, i]) => picked.has(i));
    $("pk-table").innerHTML = `<tr><th><input type="checkbox" id="pk-all" ${allOn ? "checked" : ""}></th>${list.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>`
      + visible.map(([r, i]) => {
        const dup = existing.has(emailKey(r));
        return `<tr class="${dup ? "dup" : ""}"><td><input type="checkbox" data-pk="${i}" ${dup ? "disabled" : picked.has(i) ? "checked" : ""}></td>${list.columns.map((c) => `<td>${esc(r[c] || "")}</td>`).join("")}</tr>`;
      }).join("");
    $("pk-all").onchange = (e) => { selectable.forEach(([, i]) => (e.target.checked ? picked.add(i) : picked.delete(i))); render(); };
    $("modal-actions").lastChild.textContent = picked.size ? `添加选中（${picked.size}）` : "添加选中";
  };
  $("pk-list").onchange = (e) => { current = e.target.value; picked = new Set(); render(); };
  $("pk-q").oninput = render;
  $("pk-table").onchange = (e) => {
    const i = e.target.dataset.pk; if (i === undefined) return;
    e.target.checked ? picked.add(+i) : picked.delete(+i); render();
  };
  $("pk-del").onclick = async (e) => {
    if (e.target.dataset.confirm !== "1") { e.target.dataset.confirm = "1"; e.target.textContent = "再点一次确认删除"; return; }
    try { S.lists = await api("/api/lists/delete", { name: current }); } catch (err) { return showError(err); }
    toast(`名单“${current}”已删除`);
    closeModal();
    if (Object.keys(S.lists).length) pickFromLists();
  };
  render();
}

// ---------------------------------------------------------------- cc/bcc groups
const splitEmails = (t) => String(t || "").split(/[;,；，\s]+/).filter(Boolean);
const GM = { btn: null };

function openGroupMenu(btn) {
  if (GM.btn === btn) return closeGroupMenu();
  GM.btn = btn;
  const label = btn.dataset.label;
  const names = Object.keys(S.groups).sort();
  const menu = $("grp-menu");
  menu.innerHTML = (names.length
    ? names.map((n) => `<div class="menu-item" data-use="${esc(n)}" title="${esc(S.groups[n].join("; "))}">
        <span>${esc(n)} <span class="muted small">（${S.groups[n].length} 个）</span></span>
        <button class="x" data-delgrp="${esc(n)}" title="删除此组合">×</button></div>`).join("")
    : '<div class="muted small menu-empty">还没有保存的组合。先在输入框里填好邮箱，再点下面保存。</div>')
    + `<div class="menu-sep"></div><div class="menu-item menu-save" data-save="1">＋ 把当前${esc(label)}存为组合</div>`;
  const r = btn.getBoundingClientRect();
  menu.style.left = Math.max(8, r.right - 280) + "px";
  menu.style.top = r.bottom + 4 + "px";
  menu.classList.remove("hidden");
}

function closeGroupMenu() {
  $("grp-menu").classList.add("hidden");
  GM.btn = null;
}

function useGroup(name) {
  const field = $(GM.btn.dataset.grp), label = GM.btn.dataset.label;
  const current = splitEmails(field.value);
  const seen = new Set(current.map((e) => e.toLowerCase()));
  const added = S.groups[name].filter((e) => !seen.has(e.toLowerCase()));
  field.value = [...current, ...added].join("; ");
  field.dispatchEvent(new Event("input"));
  closeGroupMenu();
  toast(added.length ? `已加入“${name}”的 ${added.length} 个邮箱` : `“${name}”的邮箱都已在${label}栏里`);
}

async function saveGroup() {
  const btn = GM.btn, label = btn.dataset.label;
  const emails = splitEmails($(btn.dataset.grp).value);
  closeGroupMenu();
  if (!emails.length) return toast(`请先在${label}栏填好邮箱，再保存为组合`, true);
  const name = await ask(`把这 ${emails.length} 个邮箱保存为组合（同名会覆盖）`, "");
  if (!name) return;
  try { S.groups = await api("/api/groups", { name, emails: emails.join("; ") }); toast(`组合“${name}”已保存`); }
  catch (e) { showError(e); }
}

async function onGroupMenuClick(e) {
  const del = e.target.closest("[data-delgrp]");
  if (del) {
    e.stopPropagation();
    if (!del.classList.contains("confirm")) { del.classList.add("confirm"); del.textContent = "确认删除"; return; }
    const name = del.dataset.delgrp, btn = GM.btn;
    try { S.groups = await api("/api/groups/delete", { name }); } catch (err) { return showError(err); }
    toast(`组合“${name}”已删除`);
    GM.btn = null; openGroupMenu(btn); // re-render the menu in place
    return;
  }
  const use = e.target.closest("[data-use]");
  if (use) return useGroup(use.dataset.use);
  if (e.target.closest("[data-save]")) return saveGroup();
}

// ---------------------------------------------------------------- content & attachments
function insertVar(name) {
  const f = lastField || $("c-body");
  const token = `{{${name}}}`;
  const s = f.selectionStart ?? f.value.length, e = f.selectionEnd ?? f.value.length;
  f.value = f.value.slice(0, s) + token + f.value.slice(e);
  f.focus(); f.selectionStart = f.selectionEnd = s + token.length;
  f.dispatchEvent(new Event("input"));
}

function renderAttachments() {
  $("att-list").innerHTML = C.attachments.map((a, i) =>
    `<li>📎 ${esc(a.name)}<span class="size">${fmtSize(a.size)}</span><button class="x" data-delatt="${i}" title="移除">×</button></li>`).join("");
}

async function addAttachments(files) {
  for (const f of files) {
    try { C.attachments.push(await api("/api/upload", f, f.name)); }
    catch (e) { showError(e); }
  }
  const total = C.attachments.reduce((n, a) => n + a.size, 0);
  if (total > 20 * 1048576) toast(`附件总大小 ${fmtSize(total)}，超过 20MB 可能会被对方邮箱拒收`, true);
  renderAttachments(); autosave();
}

function fillCompose() {
  $("c-cc").value = C.cc || ""; $("c-bcc").value = C.bcc || "";
  $("c-bcc-wrap").classList.toggle("hidden", !C.bcc);
  $("c-bcc-toggle").classList.toggle("hidden", !!C.bcc);
  $("c-subject").value = C.subject || ""; $("c-body").value = C.body || "";
  $("c-sig").checked = C.use_signature !== false;
  if (!Array.isArray(C.variables)) C.variables = [];
  renderCommonVars(); renderTable(); renderAttachments(); renderTemplates();
}

function readFields() {
  C.cc = $("c-cc").value; C.bcc = $("c-bcc").value;
  C.subject = $("c-subject").value; C.body = $("c-body").value; C.use_signature = $("c-sig").checked;
}

// ---------------------------------------------------------------- draft & templates
function autosave() {
  $("saved").textContent = "";
  clearTimeout(autosave.timer);
  autosave.timer = setTimeout(async () => {
    try { await api("/api/draft", C); $("saved").textContent = "✓ 已自动保存"; }
    catch { $("saved").textContent = "自动保存失败"; }
  }, 600);
}

function renderTemplates(selected = "") {
  const names = Object.keys(S.templates).sort();
  $("tpl-select").innerHTML = '<option value="">载入模板…</option>'
    + names.map((n) => `<option ${n === selected ? "selected" : ""}>${esc(n)}</option>`).join("");
  $("tpl-del").classList.toggle("hidden", !selected);
}

async function loadTemplate() {
  const name = $("tpl-select").value;
  if (!name) return renderTemplates();
  applyTemplate(name);
}

// Replace the current compose with a template (asks first if something is being written).
function applyTemplate(name, after) {
  const apply = () => {
    C = structuredClone(S.templates[name]); fillCompose(); renderTemplates(name); autosave();
    toast(`已载入模板：${name}`); after && after();
  };
  readFields();
  if (C.subject || C.body || filledRows().length) {
    modal(`<h3>载入模板“${esc(name)}”</h3><p>“写邮件”里当前正在编辑的内容会被替换。</p>`,
      [["取消", () => renderTemplates()], ["载入", apply, true]]);
  } else apply();
}

async function saveTemplate() {
  readFields();
  const name = await ask("模板名称（同名会覆盖）", $("tpl-select").value || C.subject.replace(/\{\{.*?\}\}/g, "").trim());
  if (!name) return;
  try { S.templates = await api("/api/templates", { name, compose: C }); renderTemplates(name); toast("模板已保存"); }
  catch (e) { showError(e); }
}

function deleteTemplate() {
  const name = $("tpl-select").value;
  modal(`<h3>删除模板“${esc(name)}”？</h3>`, [["取消"], ["删除", async () => {
    S.templates = await api("/api/templates/delete", { name }); renderTemplates(); toast("已删除");
  }, true]]);
}

function newCompose() {
  modal("<h3>新建空白邮件？</h3><p>主题、正文、收件人和附件会清空，抄送保留。</p>", [["取消"], ["新建", () => {
    C = blankCompose(C.cc); fillCompose(); autosave();
  }, true]]);
}

// ---------------------------------------------------------------- library (资料管理)
const LIB = { tab: "templates", open: null, isNew: false, edit: null };
const LIB_INFO = {
  templates: { title: "邮件模板", what: "模板", desc: "整封邮件的模板（主题、正文、收件人、抄送、通用变量、附件）。在“写邮件”底部点“存为模板”即可新建；要修改模板，载入后编辑，再用同名“存为模板”覆盖。" },
  lists: { title: "收件人名单", what: "名单", desc: "常用的供应商名单。在“写邮件”里点“从名单添加”勾选使用，或在这里直接加入当前邮件。" },
  groups: { title: "抄送/密送组合", what: "组合", desc: "常用的抄送/密送邮箱组合。在“写邮件”的抄送、密送栏右侧点“组合 ▾”一键加入。" },
};
const libData = () => S[LIB.tab] || {};

function renderLibrary() {
  for (const k of Object.keys(LIB_INFO)) $("lib-n-" + k).textContent = Object.keys(S[k] || {}).length || "";
  document.querySelectorAll(".lib-tab").forEach((b) => b.classList.toggle("active", b.dataset.lib === LIB.tab));
  if (LIB.tab === "history") return renderHistory();
  if (LIB.tab === "backup") return renderBackup();
  const info = LIB_INFO[LIB.tab];
  $("lib-title").textContent = info.title;
  $("lib-desc").textContent = info.desc;
  $("lib-tools").innerHTML = {
    templates: "",
    lists: '<button data-act="new">新建名单</button><button data-act="import">从 Excel/CSV 导入为新名单</button>',
    groups: '<button data-act="new">新建组合</button>',
  }[LIB.tab];

  const names = Object.keys(libData()).sort();
  if (LIB.isNew && LIB.open && !names.includes(LIB.open)) names.unshift(LIB.open);
  $("lib-body").innerHTML = names.length ? names.map(libItem).join("")
    : `<div class="lib-empty">还没有保存的${info.what}。</div>`;
  const first = $("lib-body").querySelector(".lib-editor input, .lib-editor textarea");
  if (LIB.isNew && first) first.focus();
}

function libItem(name) {
  const open = LIB.open === name;
  const n = esc(name);
  let meta = "", actions = "", editor = "";
  if (LIB.tab === "templates") {
    const t = S.templates[name];
    const rows = (t.rows || []).filter((r) => (t.columns || []).some((c) => String(r[c] || "").trim()));
    meta = `主题：${esc(t.subject || "（无）")} · 收件人 ${rows.length} 位 · 附件 ${(t.attachments || []).length} 个`;
    actions = `<button data-act="use" class="primary">用此模板写邮件</button><button data-act="toggle">${open ? "收起" : "详情"}</button>`;
    if (open) {
      const vars = (t.variables || []).filter((v) => v.name.trim()).map((v) => `${v.name} = ${v.value || "（空）"}`).join("\n");
      editor = `<dl class="lib-detail">
        <dt>抄送</dt><dd>${esc(t.cc || "无")}</dd>
        ${t.bcc ? `<dt>密送</dt><dd>${esc(t.bcc)}</dd>` : ""}
        <dt>收件人</dt><dd>${rows.length ? esc(rows.map((r) => r[EMAIL_COL] + (r["公司"] ? `（${r["公司"]}）` : "")).join("\n")) : "无"}</dd>
        ${vars ? `<dt>通用变量</dt><dd>${esc(vars)}</dd>` : ""}
        <dt>附件</dt><dd>${esc((t.attachments || []).map((a) => a.name).join("、") || "无")}</dd>
        <dt>正文</dt><dd>${esc(t.body || "")}</dd></dl>`;
    }
  } else if (LIB.tab === "lists") {
    const l = S.lists[name];
    meta = l ? `${l.rows.length} 位收件人 · 列：${esc(l.columns.join("、"))}` : "新名单（未保存）";
    actions = (l ? '<button data-act="addto">加入当前邮件</button>' : "") + `<button data-act="toggle">${open ? "收起" : "编辑"}</button>`;
    if (open) editor = listEditor();
  } else {
    const g = S.groups[name];
    meta = g ? `${g.length} 个邮箱：${esc(g.join("; "))}` : "新组合（未保存）";
    actions = `<button data-act="toggle">${open ? "收起" : "编辑"}</button>`;
    if (open) editor = `<label class="small">每行一个邮箱（也可以用 ; 分隔）</label>
      <textarea id="lib-grp-text" rows="5" placeholder="boss@company.com&#10;tech@company.com">${esc(LIB.edit)}</textarea>
      <div class="lib-editor-actions"><span></span><span><button data-act="cancel">取消</button> <button data-act="save" class="primary">保存</button></span></div>`;
  }
  const exists = name in libData();
  if (exists) actions += '<button data-act="rename">重命名</button><button data-act="del" class="danger">删除</button>';
  return `<div class="lib-item ${open ? "open" : ""}" data-name="${n}">
    <div class="lib-row"><div class="lib-main"><div class="lib-name">${n}</div><div class="lib-meta">${meta}</div></div>
    <div class="lib-actions">${actions}</div></div>
    ${editor ? `<div class="lib-editor">${editor}</div>` : ""}</div>`;
}

function listEditor() {
  const { columns, rows } = LIB.edit;
  const head = `<tr><th></th>${columns.map((c, i) => `<th>${esc(c)}${i ? ` <button class="x" data-lcol="${i}" title="删除此列">×</button>` : ' <span class="danger">*</span>'}</th>`).join("")}<th class="del"></th></tr>`;
  const body = rows.map((r, ri) => `<tr><td class="idx">${ri + 1}</td>
    ${columns.map((c, ci) => `<td><input data-lr="${ri}" data-lc="${ci}" value="${esc(r[c] || "")}"${ci === 0 ? ' placeholder="name@company.com"' : ""}></td>`).join("")}
    <td class="del"><button class="x" data-lrow="${ri}" title="删除此行">×</button></td></tr>`).join("");
  return `<div class="table-wrap"><table>${head}${body}</table></div>
    <div class="lib-editor-actions">
      <span><button data-act="addrow" class="link">＋ 添加一行</button><button data-act="addcol" class="link">＋ 添加列</button>
        <span class="muted small">可从 Excel 复制多行粘贴</span></span>
      <span><button data-act="cancel">取消</button> <button data-act="save" class="primary">保存</button></span></div>`;
}

function openEditor(name, isNew) {
  LIB.open = name; LIB.isNew = !!isNew;
  if (LIB.tab === "lists") {
    const l = S.lists[name] || { columns: [EMAIL_COL, "公司", "联系人"], rows: [] };
    LIB.edit = structuredClone(l);
    LIB.edit.rows.push({}, {});
  } else if (LIB.tab === "groups") {
    LIB.edit = (S.groups[name] || []).join("\n");
  }
  renderLibrary();
}

function closeEditor() { LIB.open = null; LIB.isNew = false; LIB.edit = null; renderLibrary(); }

async function libSave(name) {
  try {
    if (LIB.tab === "lists") {
      const rows = LIB.edit.rows.filter((r) => LIB.edit.columns.some((c) => String(r[c] || "").trim()));
      if (!rows.length) return toast("名单里至少要有一位收件人", true);
      S.lists = await api("/api/lists", { name, list: { columns: LIB.edit.columns, rows } });
    } else {
      S.groups = await api("/api/groups", { name, emails: $("lib-grp-text").value });
    }
    toast(`${LIB_INFO[LIB.tab].what}“${name}”已保存`);
    closeEditor();
  } catch (e) { showError(e); }
}

async function libRename(name) {
  const what = LIB_INFO[LIB.tab].what;
  const next = await ask(`重命名${what}“${name}”`, name);
  if (!next || next === name) return;
  try {
    const res = await api("/api/rename", { kind: LIB.tab, old: name, new: next });
    S[LIB.tab] = res[LIB.tab];
    if (LIB.open === name) LIB.open = next;
    if (LIB.tab === "templates") renderTemplates();
    renderLibrary(); toast("已重命名");
  } catch (e) { showError(e); }
}

async function libDelete(btn, name) {
  if (!btn.classList.contains("armed")) {
    btn.classList.add("armed"); btn.textContent = "确认删除";
    setTimeout(() => { if (btn.isConnected) { btn.classList.remove("armed"); btn.textContent = "删除"; } }, 3000);
    return;
  }
  const path = { templates: "/api/templates/delete", lists: "/api/lists/delete", groups: "/api/groups/delete" }[LIB.tab];
  try {
    S[LIB.tab] = await api(path, { name });
    if (LIB.tab === "templates") renderTemplates();
    if (LIB.open === name) LIB.open = null;
    renderLibrary(); toast(`已删除“${name}”`);
  } catch (e) { showError(e); }
}

function libAddToCompose(name) {
  const l = S.lists[name];
  const existing = new Set(filledRows().map(emailKey));
  const fresh = l.rows.filter((r) => !existing.has(emailKey(r)));
  l.columns.forEach((c) => { if (!C.columns.includes(c)) C.columns.push(c); });
  C.rows = [...filledRows(), ...fresh.map((r) => ({ ...r }))];
  if (!C.rows.length) C.rows.push({});
  renderTable(); autosave();
  const skipped = l.rows.length - fresh.length;
  toast(`已加入 ${fresh.length} 位收件人到“写邮件”${skipped ? `，跳过重复 ${skipped} 位` : ""}`);
}

async function libNew() {
  const what = LIB_INFO[LIB.tab].what;
  const name = await ask(`新${what}的名称`, "");
  if (!name) return;
  if (name in libData()) return toast(`已经有名为“${name}”的${what}了`, true);
  openEditor(name, true);
}

async function libImport() {
  const [file] = await pickFiles($("lib-file"));
  if (!file) return;
  try {
    const { columns, rows } = await importFile(file);
    const name = await ask(`把 ${rows.length} 位收件人保存为名单`, file.name.replace(/\.[^.]+$/, ""));
    if (!name) return;
    if (name in S.lists) return toast(`已经有名为“${name}”的名单了，请换个名字`, true);
    S.lists = await api("/api/lists", { name, list: { columns, rows } });
    toast(`名单“${name}”已保存（${rows.length} 位）`);
    renderLibrary();
  } catch (e) { showError(e); }
}

async function onLibClick(e) {
  if (LIB.tab === "history") return onHistClick(e);
  if (LIB.tab === "backup") return onBackupClick(e);
  const btn = e.target.closest("button"); if (!btn) return;
  const item = btn.closest(".lib-item"), name = item?.dataset.name;
  const act = btn.dataset.act;
  if (act === "new") return libNew();
  if (act === "import") return libImport();
  if (act === "use") return applyTemplate(name, () => go("compose"));
  if (act === "toggle") return LIB.open === name ? closeEditor() : openEditor(name);
  if (act === "rename") return libRename(name);
  if (act === "del") return libDelete(btn, name);
  if (act === "addto") return libAddToCompose(name);
  if (act === "cancel") return closeEditor();
  if (act === "save") return libSave(name);
  if (!LIB.edit || LIB.tab !== "lists") return;
  const ed = LIB.edit;
  if (act === "addrow") { ed.rows.push({}); renderLibrary(); return; }
  if (act === "addcol") {
    const col = await ask("新列的名称", "");
    if (!col) return;
    if (/[{}\s]/.test(col)) return toast("列名不能包含空格或花括号", true);
    if (ed.columns.includes(col)) return toast("已有同名列", true);
    ed.columns.push(col); renderLibrary(); return;
  }
  if (btn.dataset.lrow !== undefined) { ed.rows.splice(+btn.dataset.lrow, 1); if (!ed.rows.length) ed.rows.push({}); renderLibrary(); }
  if (btn.dataset.lcol !== undefined) {
    const col = ed.columns[+btn.dataset.lcol];
    ed.columns.splice(+btn.dataset.lcol, 1); ed.rows.forEach((r) => delete r[col]); renderLibrary();
  }
}

function onLibInput(e) {
  const t = e.target;
  if (t.dataset.lr !== undefined) LIB.edit.rows[+t.dataset.lr][LIB.edit.columns[+t.dataset.lc]] = t.value;
}

function onLibPaste(e) {
  const t = e.target; if (t.dataset.lr === undefined) return;
  const text = e.clipboardData.getData("text");
  if (!/[\t\n]/.test(text.trim())) return;
  e.preventDefault();
  const ed = LIB.edit, lines = text.replace(/\r/g, "").replace(/\n+$/, "").split("\n");
  const r0 = +t.dataset.lr, c0 = +t.dataset.lc;
  lines.forEach((line, i) => {
    while (ed.rows.length <= r0 + i) ed.rows.push({});
    line.split("\t").forEach((cell, j) => { const col = ed.columns[c0 + j]; if (col) ed.rows[r0 + i][col] = cell.trim(); });
  });
  renderLibrary(); toast(`已粘贴 ${lines.length} 行`);
}

// ---------------------------------------------------------------- send records (资料管理 → 发送记录)
const HIST = { list: null, open: null, detail: null, q: "" };
const fileUrl = (id, params) => `/api/history/file?id=${encodeURIComponent(id)}&${params}&token=${encodeURIComponent(window.TOKEN)}`;

async function renderHistory() {
  $("lib-title").textContent = "发送记录";
  $("lib-desc").textContent = "每次正式发送都会自动记录：发给了谁、主题、抄送、附件、每封的结果和邮件原文（测试邮件不记录）。"
    + "完整邮件（含附件）同时存入了邮箱的“已发送”文件夹。";
  $("lib-tools").innerHTML = `<input id="hist-q" type="text" placeholder="搜索主题、收件人、公司…" value="${esc(HIST.q)}" style="width:260px;margin:0">`;
  $("hist-q").oninput = (e) => { HIST.q = e.target.value; renderHistoryBody(); };
  if (!HIST.list) {
    $("lib-body").innerHTML = '<div class="lib-empty">加载中…</div>';
    try { HIST.list = (await api("/api/history")).batches; }
    catch (e) { $("lib-body").innerHTML = ""; return showError(e); }
  }
  if (HIST.open && HIST.detail?.id !== HIST.open) await loadHistoryDetail(HIST.open);
  renderHistoryBody();
}

async function loadHistoryDetail(id) {
  try { HIST.detail = await api("/api/history/batch?id=" + encodeURIComponent(id)); }
  catch (e) { HIST.open = null; showError(e); }
}

function renderHistoryBody() {
  if (LIB.tab !== "history") return;
  $("lib-n-history").textContent = HIST.list.length || "";
  const q = HIST.q.trim().toLowerCase();
  const shown = HIST.list.filter((b) => !q || b.search.includes(q));
  if (!shown.length) {
    $("lib-body").innerHTML = `<div class="lib-empty">${HIST.list.length ? "没有符合条件的记录。" : "还没有发送记录。正式发送邮件后会自动出现在这里。"}</div>`;
    return;
  }
  $("lib-body").innerHTML = shown.map((b) => {
    const open = HIST.open === b.id;
    const meta = [b.time, `共 ${b.total} 封`, `<span class="st-sent">成功 ${b.sent}</span>`]
      .concat(b.failed ? [`<span class="st-failed">失败 ${b.failed}</span>`] : [])
      .concat(b.total - b.sent - b.failed > 0 ? [`未完成 ${b.total - b.sent - b.failed}`] : [])
      .concat([`已存入已发送 ${b.archived} 封`])
      .concat(b.attachments.length ? [`附件：${esc(b.attachments.join("、"))}`] : []).join(" · ");
    return `<div class="lib-item ${open ? "open" : ""}" data-hid="${esc(b.id)}">
      <div class="lib-row"><div class="lib-main"><div class="lib-name">${esc(b.subject || b.first_subject)}</div><div class="lib-meta">${meta}</div></div>
      <div class="lib-actions"><button data-hact="toggle">${open ? "收起" : "详情"}</button></div></div>
      ${open && HIST.detail?.id === b.id ? `<div class="lib-editor">${historyDetail(HIST.detail)}</div>` : ""}</div>`;
  }).join("");
}

function historyDetail(b) {
  const vars = Object.entries(b.variables || {}).map(([k, v]) => `${k} = ${v}`).join("\n");
  const atts = b.attachments.map((a) => `<a href="${fileUrl(b.id, "kind=att&name=" + encodeURIComponent(a.name))}" download>${esc(a.name)}</a> <span class="muted small">${fmtSize(a.size)}</span>`).join("<br>");
  const status = (m) => m.status === "sent" ? `<span class="st-sent">✓ 已发送</span>${m.error ? `<div class="warn">⚠ ${esc(m.error)}</div>` : ""}`
    : m.status === "failed" ? `<span class="st-failed">✗ 失败</span><div class="muted small">${esc(m.error)}</div>`
    : '<span class="muted">未发送</span>';
  const archived = (m) => m.archived === "saved" ? '<span class="st-sent">✓</span>' : m.archived === "failed" ? '<span class="st-failed">✗</span>' : '<span class="muted">—</span>';
  const company = (m) => m.fields["公司"] || m.fields["供应商"] || "";
  return `<dl class="lib-detail">
      <dt>发件人</dt><dd>${esc(b.from)}</dd>
      <dt>抄送</dt><dd>${esc(b.cc || "无")}</dd>
      ${b.bcc ? `<dt>密送</dt><dd>${esc(b.bcc)}</dd>` : ""}
      ${vars ? `<dt>通用变量</dt><dd>${esc(vars)}</dd>` : ""}
      <dt>附件</dt><dd>${atts || "无"}</dd></dl>
    <div class="table-wrap" style="margin-top:12px"><table class="summary" style="margin:0;border:0">
      <tr><th>#</th><th>收件人</th><th>公司</th><th>主题</th><th>结果</th><th title="是否已存入邮箱的“已发送”文件夹">已发送夹</th><th>时间</th><th></th></tr>
      ${b.mails.map((m) => `<tr><td>${m.n}</td><td>${esc(m.to)}</td><td>${esc(company(m))}</td><td>${esc(m.subject)}</td>
        <td>${status(m)}</td><td style="text-align:center">${archived(m)}</td><td class="muted small">${esc(m.time.slice(5, 16))}</td>
        <td style="white-space:nowrap"><button data-hact="view" data-n="${m.n}" class="link">查看</button></td></tr>`).join("")}
    </table></div>`;
}

async function viewRecordedMail(id, n) {
  let m;
  try { m = await api(`/api/history/mail?id=${encodeURIComponent(id)}&n=${n}`); }
  catch (e) { return showError(e); }
  modal(`<h3>${esc(m.subject)}</h3>
    <div class="preview-meta" style="font-size:13px;line-height:1.8">
      <div><b>发件人：</b>${esc(m.from)}</div><div><b>收件人：</b>${esc(m.to)}</div>
      ${m.cc ? `<div><b>抄送：</b>${esc(m.cc)}</div>` : ""}${m.bcc ? `<div><b>密送：</b>${esc(m.bcc)}</div>` : ""}
      <div><b>时间：</b>${esc(m.date)}</div>${m.attachments ? `<div><b>附件：</b>${esc(m.attachments)}</div>` : ""}
      <div><a href="${fileUrl(id, "kind=eml&n=" + n)}" download>下载原始邮件（.eml，可用 Foxmail 打开）</a></div>
    </div>
    <iframe id="hist-frame" sandbox style="width:100%;height:50vh;border:1px solid var(--border);border-radius:8px;margin-top:10px"></iframe>`,
    [["关闭", null, true]]);
  $("hist-frame").srcdoc = m.html;
}

async function onHistClick(e) {
  const btn = e.target.closest("button[data-hact]"); if (!btn) return;
  const id = btn.closest("[data-hid]").dataset.hid;
  if (btn.dataset.hact === "view") return viewRecordedMail(id, btn.dataset.n);
  if (HIST.open === id) { HIST.open = null; return renderHistoryBody(); }
  HIST.open = id;
  await loadHistoryDetail(id);
  renderHistoryBody();
}

// ---------------------------------------------------------------- backup & restore (资料管理 → 备份与恢复)
const BK = { data: null, picked: null };
const BK_KIND = { manual: "手动导出", auto: "自动备份", imported: "导入的备份" };

function localCounts() {
  const n = (o) => Object.keys(o || {}).length;
  return {
    account: S.account ? 1 : 0,
    signature: S.signature && (S.signature.text || S.signature.logo) ? 1 : 0,
    templates: n(S.templates), lists: n(S.lists), groups: n(S.groups),
    draft: C && (C.subject || C.body) ? 1 : 0,
    history: HIST.list ? HIST.list.length : null,
  };
}

async function renderBackup() {
  $("lib-title").textContent = "备份与恢复";
  $("lib-desc").textContent = "把资料打包成一个 .zip 文件，保存在项目的 backups 文件夹里；再把它拷贝到网盘，电脑出问题也能恢复。"
    + "授权码不会被导出，恢复后需在“账号设置”重新输入。";
  $("lib-tools").innerHTML = '<button data-bact="upload">导入其他位置的备份…</button><button data-bact="reveal">打开备份文件夹</button>';
  $("lib-body").innerHTML = '<div class="lib-empty">加载中…</div>';
  try { BK.data = await api("/api/backups"); }
  catch (e) { $("lib-body").innerHTML = ""; return showError(e); }
  if (LIB.tab === "backup") renderBackupBody();
}

function renderBackupBody() {
  const { items, files, folder } = BK.data;
  const counts = localCounts();
  if (!BK.picked) BK.picked = new Set(Object.keys(items));
  const boxes = Object.entries(items).map(([k, label]) => {
    const n = counts[k];
    const hint = n === null ? "" : k === "templates" || k === "lists" || k === "groups" ? `（${n} 个）` : k === "history" ? `（${n} 批）` : n ? "" : "（无）";
    return `<label><input type="checkbox" data-bitem="${k}" ${BK.picked.has(k) ? "checked" : ""}> ${esc(label)}<span class="muted small">${hint}</span></label>`;
  }).join("");
  const list = files.length ? files.map((f) => {
    const what = Object.values(f.items).map((i) => `${i.label}${i.count > 1 ? " " + i.count : ""}`).join("、");
    return `<div class="lib-item" data-bname="${esc(f.name)}"><div class="lib-row">
      <div class="lib-main"><div class="lib-name">${esc(f.name.split("/").pop())}</div>
        <div class="lib-meta"><span class="kind-${f.kind}">${BK_KIND[f.kind]}</span> · ${esc(f.created)} · ${fmtSize(f.size)}${f.account ? " · " + esc(f.account) : ""} · 包含：${esc(what) || "无"}</div></div>
      <div class="lib-actions"><button data-bact="restore">恢复…</button><button data-bact="show">在文件夹中显示</button></div></div></div>`;
  }).join("") : '<div class="lib-empty">还没有备份文件。</div>';
  $("lib-body").innerHTML = `
    <h3 class="bk-h">导出备份</h3>
    <div class="bk-box">
      <div class="bk-items">${boxes}</div>
      <div class="lib-editor-actions"><span class="muted small">保存到：${esc(folder)}<br>只导出部分内容（如模板、名单）也可以用来分享给同事。</span>
        <button data-bact="export" class="primary">导出备份</button></div>
    </div>
    <h3 class="bk-h">备份文件 <span class="muted small">（每天第一次打开时自动备份一次，恢复前也会自动备份，自动备份保留最近 7 份）</span></h3>
    ${list}`;
  $("lib-body").querySelectorAll("[data-bitem]").forEach((cb) => (cb.onchange = () => {
    cb.checked ? BK.picked.add(cb.dataset.bitem) : BK.picked.delete(cb.dataset.bitem);
  }));
}

async function exportBackup(btn) {
  if (!BK.picked.size) return toast("请至少勾选一项要导出的内容", true);
  try {
    const made = await busy(btn, () => api("/api/backups/export", { items: [...BK.picked] }));
    modal(`<h3>备份已导出</h3><p>文件：<b>${esc(made.name)}</b>（${fmtSize(made.size)}）</p>
      <p class="muted small">位置：${esc(made.path)}</p>
      <p>请把这个文件拷贝到网盘保存。换电脑时，在这里点“导入其他位置的备份…”选择它即可恢复。</p>`,
      [["在文件夹中显示", () => { api("/api/backups/reveal", { name: made.name }).catch(showError); }], ["好的", null, true]]);
    renderBackup();
  } catch (e) { showError(e); }
}

function restoreDialog(name, manifest) {
  const items = Object.entries(manifest.items);
  if (!items.length) return toast("这个备份里没有可恢复的内容", true);
  modal(`<h3>从备份恢复</h3>
    <p class="muted small">${esc(name.split("/").pop())} · 创建于 ${esc(manifest.created)}${manifest.account ? " · " + esc(manifest.account) : ""}</p>
    <div class="bk-box"><div class="bk-items">${items.map(([k, i]) =>
      `<label><input type="checkbox" data-ritem="${k}" checked> ${esc(i.label)}<span class="muted small">${i.count > 1 ? `（${i.count}）` : ""}</span></label>`).join("")}</div></div>
    <div class="bk-mode">
      <label><input type="radio" name="bk-mode" value="merge" checked><span><b>合并（推荐）</b><br><span class="muted small">保留现有资料，只添加备份里有、这里没有的。同名但内容不同的会以“名称（导入）”加入，不会覆盖。账号、签名、草稿只在这里为空时恢复。</span></span></label>
      <label><input type="radio" name="bk-mode" value="replace"><span><b>替换</b><br><span class="muted small">用备份中的内容覆盖所选项目（例如回到某次备份时的模板）。</span></span></label>
    </div>
    <p class="muted small">恢复前会自动备份一次当前资料，恢复错了可以撤回。发送记录始终只增不删。</p>`,
    [["取消"], ["开始恢复", async (btn) => {
      const chosen = [...document.querySelectorAll("[data-ritem]")].filter((c) => c.checked).map((c) => c.dataset.ritem);
      if (!chosen.length) { toast("请至少勾选一项", true); return false; }
      const mode = document.querySelector('input[name="bk-mode"]:checked').value;
      try {
        const res = await busy(btn, () => api("/api/backups/restore", { name, items: chosen, mode }));
        await reloadAfterRestore();
        modal(`<h3>恢复完成</h3><table class="summary">${Object.entries(res.summary).map(([k, v]) =>
          `<tr><td width="120"><b>${esc(k)}</b></td><td>${esc(v)}</td></tr>`).join("")}</table>
          <p class="muted small">恢复前的资料已自动备份为“${esc(res.snapshot)}”。如需撤销，用它以“替换”方式恢复即可。</p>`,
          [["好的", null, true]]);
        renderBackup();
      } catch (e) { showError(e); }
      return false; // the summary dialog replaces this one
    }, true]]);
}

async function reloadAfterRestore() {
  const fresh = await api("/api/state");
  Object.assign(S, fresh);
  if (fresh.draft) C = fresh.draft;
  if (!C.rows?.length) C.rows = [{}];
  fillCompose(); renderWho(); renderTemplates();
  HIST.list = null; HIST.open = null;
}

async function onBackupClick(e) {
  const btn = e.target.closest("button[data-bact]"); if (!btn) return;
  const act = btn.dataset.bact, name = btn.closest("[data-bname]")?.dataset.bname;
  try {
    if (act === "export") return exportBackup(btn);
    if (act === "reveal") return await api("/api/backups/reveal", {});
    if (act === "show") return await api("/api/backups/reveal", { name });
    if (act === "restore") return restoreDialog(name, await api("/api/backups/inspect", { name }));
    if (act === "upload") {
      const [file] = await pickFiles($("bk-file"));
      if (!file) return;
      const up = await api("/api/backups/upload", file, file.name);
      restoreDialog(up.name, up.manifest);
      renderBackup();
    }
  } catch (err) { showError(err); }
}

// ---------------------------------------------------------------- preview & send
async function preview(btn) {
  readFields();
  try {
    const { mails } = await busy(btn, () => api("/api/preview", C));
    const show = (i) => {
      const m = mails[i];
      document.querySelectorAll("#pv-list li").forEach((li, j) => li.classList.toggle("active", i === j));
      $("pv-meta").innerHTML = `<div><b>收件人：</b>${esc(m.to)}</div>${m.cc ? `<div><b>抄送：</b>${esc(m.cc)}</div>` : ""}
        <div><b>主题：</b>${esc(m.subject)}</div>${C.attachments.length ? `<div><b>附件：</b>${C.attachments.map((a) => esc(a.name)).join("、")}</div>` : ""}`;
      $("pv-frame").srcdoc = m.html;
    };
    modal(`<h3>预览（共 ${mails.length} 封）</h3><div class="preview">
      <ul id="pv-list">${mails.map((m, i) => `<li data-i="${i}">${i + 1}. ${esc(m.to)}</li>`).join("")}</ul>
      <div><div id="pv-meta" class="meta"></div><iframe id="pv-frame" sandbox></iframe></div></div>`, [["关闭", null, true]]);
    $("pv-list").onclick = (e) => { if (e.target.dataset.i) show(+e.target.dataset.i); };
    show(0);
  } catch (e) { showError(e); }
}

async function confirmSend(btn) {
  readFields();
  try {
    const { mails } = await busy(btn, () => api("/api/preview", C));
    const att = C.attachments.map((a) => esc(a.name)).join("、") || "无";
    modal(`<h3>确认发送 ${mails.length} 封独立邮件</h3>
      <p class="muted">从 ${esc(S.account.email)} 发出；每位收件人单独收到一封，彼此看不到。</p>
      <table class="summary">
        <tr><td width="90"><b>抄送</b></td><td>${esc(C.cc) || "无"}</td></tr>
        ${C.bcc ? `<tr><td><b>密送</b></td><td>${esc(C.bcc)}</td></tr>` : ""}
        <tr><td><b>附件</b></td><td>${att}</td></tr>
        <tr><td><b>签名</b></td><td>${C.use_signature ? "附带" : "不附带"}</td></tr>
      </table>
      <table class="summary"><tr><th>#</th><th>收件人</th><th>主题</th></tr>
        ${mails.map((m, i) => `<tr><td>${i + 1}</td><td>${esc(m.to)}</td><td>${esc(m.subject)}</td></tr>`).join("")}</table>`,
      [["取消"], ["确认发送", () => { startSend({ compose: C }, mails.map((m) => m.to)); }, true]]);
  } catch (e) { showError(e); }
}

function testSend() {
  readFields();
  modal(`<h3>发一封测试邮件给自己</h3><p>将用<b>第一位收件人</b>的内容（含附件、签名）生成一封邮件，只发到 <b>${esc(S.account.email)}</b>，不抄送任何人，主题前加“[测试]”。</p>`,
    [["取消"], ["发送测试", () => { startSend({ compose: C, test: true }, [S.account.email]); }, true]]);
}

async function startSend(req, targets, path = "/api/send") {
  let job;
  try { ({ job } = await api(path, req)); }
  catch (e) { return showError(e); }
  const rows = targets.map((t, i) => `<tr><td>${i + 1}</td><td>${esc(t)}</td><td id="st-${i}" class="st-wait">等待中</td></tr>`).join("");
  modal(`<h3 id="snd-title">正在发送…</h3><div class="progress"><div id="snd-bar" style="width:0"></div></div>
    <p id="snd-msg" class="muted small">为避免被判为垃圾邮件，每封之间间隔 2 秒。请不要关闭启动窗口。</p>
    <table class="summary"><tr><th>#</th><th>收件人</th><th>状态</th></tr>${rows}</table>`, []);

  const poll = async () => {
    let st;
    try { st = await api("/api/send/status?job=" + job); }
    catch (e) { return setTimeout(poll, 1500); }
    st.results.forEach((r, i) => {
      const cell = $("st-" + i); if (!cell) return;
      cell.className = "st-" + r.status;
      cell.textContent = r.status === "sent"
        ? "✓ 已发送" + (r.archived === "saved" ? " · 已存入已发送" : "") + (r.error ? "（⚠ " + r.error + "）" : "")
        : "✗ " + r.error;
    });
    $("snd-bar").style.width = Math.round((st.results.length / st.total) * 100) + "%";
    if (!st.done) return setTimeout(poll, 800);

    const ok = st.results.filter((r) => r.status === "sent").length;
    const failed = st.results.filter((r) => r.status === "failed").length;
    $("snd-title").textContent = st.error ? "发送中断" : failed ? `完成：成功 ${ok}，失败 ${failed}` : `全部发送成功（${ok} 封）`;
    const recorded = st.batch ? "本次发送已记录，可在“资料管理 → 发送记录”中查看。" : "";
    $("snd-msg").textContent = st.error || ((failed ? "可以检查失败原因后点击“重试失败项”。" : "") + recorded);
    $("snd-msg").className = st.error ? "danger" : "muted small";
    if (st.batch) HIST.list = null; // reload the record list next time it is shown
    const buttons = [["关闭", null, true]];
    if (st.batch) buttons.unshift(["查看发送记录", () => { LIB.tab = "history"; HIST.open = st.batch; go("library"); }]);
    if (failed && !st.error) {
      const failedTargets = st.results.filter((r) => r.status === "failed").map((r) => r.to);
      buttons.unshift(["重试失败项", () => { startSend({ job }, failedTargets, "/api/send/retry"); }]);
    }
    const box = $("modal-actions"); box.innerHTML = "";
    for (const [label, fn, primary] of buttons) {
      const b = document.createElement("button"); b.textContent = label; if (primary) b.className = "primary";
      b.onclick = () => { closeModal(); fn && fn(); };
      box.appendChild(b);
    }
  };
  poll();
}

// ---------------------------------------------------------------- wiring
function wire() {
  document.querySelectorAll(".nav").forEach((b) => (b.onclick = () => go(b.dataset.view)));
  document.addEventListener("click", (e) => {
    const g = e.target.closest("[data-goto]"); if (g) { e.preventDefault(); go(g.dataset.goto); }
    const v = e.target.closest("[data-var]"); if (v) insertVar(v.dataset.var);
    const a = e.target.closest("[data-delatt]");
    if (a) { C.attachments.splice(+a.dataset.delatt, 1); renderAttachments(); autosave(); }
  });

  $("acc-email").addEventListener("input", () => onEmailChange(true));
  $("acc-ent").onchange = onEntChange;
  $("acc-save").onclick = saveAccount;

  $("sig-text").addEventListener("input", renderSigPreview);
  $("sig-logo-btn").onclick = pickLogo;
  $("sig-logo-del").onclick = () => { sigLogo = null; renderSigPreview(); };
  $("sig-save").onclick = saveSignature;

  const table = $("rcpt-table");
  table.addEventListener("input", onCellInput);
  table.addEventListener("paste", onCellPaste);
  table.addEventListener("click", onTableClick);
  $("rcpt-addrow").onclick = () => { C.rows.push({}); renderTable(); table.querySelector(`input[data-r="${C.rows.length - 1}"]`).focus(); };
  $("rcpt-addcol").onclick = addColumn;
  $("rcpt-import").onclick = importTable;
  $("rcpt-pick").onclick = pickFromLists;
  document.querySelectorAll(".lib-tab").forEach((b) => (b.onclick = () => { LIB.tab = b.dataset.lib; closeEditor(); }));
  for (const id of ["lib-tools", "lib-body"]) $(id).addEventListener("click", onLibClick);
  $("lib-body").addEventListener("input", onLibInput);
  $("lib-body").addEventListener("paste", onLibPaste);
  document.querySelectorAll(".grp-btn").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); openGroupMenu(b); }));
  $("grp-menu").addEventListener("click", onGroupMenuClick);
  document.addEventListener("click", (e) => { if (GM.btn && !e.target.closest("#grp-menu")) closeGroupMenu(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && GM.btn) closeGroupMenu(); });
  window.addEventListener("scroll", () => GM.btn && closeGroupMenu(), { passive: true });
  $("rcpt-savelist").onclick = saveList;

  $("gv-list").addEventListener("input", onCommonVarInput);
  $("gv-list").addEventListener("click", (e) => {
    const i = e.target.dataset.delgv; if (i === undefined) return;
    C.variables.splice(+i, 1); renderCommonVars(); renderVars(); autosave();
  });
  $("gv-add").onclick = () => {
    C.variables.push({ name: "", value: "" }); renderCommonVars();
    $("gv-list").querySelector(`input[data-gv="${C.variables.length - 1}"]`).focus();
  };
  $("rcpt-clear").onclick = () => modal("<h3>清空所有收件人？</h3>", [["取消"], ["清空", () => {
    C.rows = [{}, {}, {}]; renderTable(); autosave();
  }, true]]);

  for (const id of ["c-cc", "c-bcc", "c-subject", "c-body"]) {
    $(id).addEventListener("input", () => { readFields(); autosave(); });
  }
  for (const id of ["c-subject", "c-body"]) {
    const f = $(id);
    f.addEventListener("focus", () => (lastField = f));
    f.addEventListener("input", (e) => { onFieldInput(e); renderWarnings(); });
    f.addEventListener("keydown", onFieldKey);
    f.addEventListener("blur", () => setTimeout(hideAC, 150));
  }
  $("ac").addEventListener("mousedown", (e) => {
    const li = e.target.closest("[data-ac]"); if (!li) return;
    e.preventDefault(); chooseAC(+li.dataset.ac);
  });

  $("btn-quit").onclick = () => modal("<h3>退出邮件助手？</h3><p>后台程序会关闭，此页面将无法继续使用。下次双击“邮件助手”即可重新打开，所有设置和草稿都已保存。</p>",
    [["取消"], ["退出", async () => {
      try { await api("/api/shutdown", {}); } catch (e) { showError(e); return false; }
      document.body.innerHTML = '<div class="bye">邮件助手已退出，可以关闭此页面。</div>';
    }, true]]);
  $("c-sig").onchange = () => { readFields(); autosave(); };
  $("c-bcc-toggle").onclick = () => { $("c-bcc-wrap").classList.remove("hidden"); $("c-bcc-toggle").classList.add("hidden"); $("c-bcc").focus(); };

  const drop = $("att-drop");
  drop.onclick = async () => addAttachments(await pickFiles($("att-file"), true));
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); addAttachments([...e.dataTransfer.files]); };

  $("tpl-select").onchange = loadTemplate;
  $("tpl-save").onclick = saveTemplate;
  $("tpl-del").onclick = deleteTemplate;
  $("c-new").onclick = newCompose;
  $("btn-preview").onclick = (e) => preview(e.currentTarget);
  $("btn-test").onclick = testSend;
  $("btn-send").onclick = (e) => confirmSend(e.currentTarget);
}

async function init() {
  wire();
  try { S = await api("/api/state"); }
  catch (e) { return toast(e.message, true); }
  C = S.draft || blankCompose();
  if (!C.rows?.length) C.rows = [{}];
  fillCompose(); renderWho();
  setInterval(() => api("/api/ping", {}).catch(() => {}), 30000); // keeps the background program alive
  go(S.account ? "compose" : "account");
}
init();
