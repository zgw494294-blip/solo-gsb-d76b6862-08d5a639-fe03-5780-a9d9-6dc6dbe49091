"use strict";

/* 异文对读台前端 */

const MAX_POINTS = 300;
const MIN_WITNESSES = 2;
const MAX_WITNESSES = 4;

const KIND_LABEL = {
  match: "相同",
  substitute: "替换",
  delete: "缺字",
  insert: "插入",
};

const EXAMPLE = {
  base: "学而时习之，不亦说乎？有朋自远方来，不亦乐乎？",
  witnesses: [
    { name: "甲本", text: "学而时习之，不亦悦乎？有朋自远方来，不亦乐乎？" },
    { name: "乙本", text: "学而时习之，不亦说乎？有友自远方来，不亦乐乎？" },
    { name: "丙本", text: "学而时习之，不亦说乎？有朋自远方来，不亦乐。" },
  ],
  query: "学而时习之，不亦悦乎？有友自远方来，不亦乐乎？",
};

let lastResult = null;
let lastInput = null; // 最近一次对读的 {base, witnesses}，供校定请求复用
let selections = {}; // "type:index" -> 所选读法；仅存非默认选择
let lastEdition = null;
let lastProv = null;
let lastRel = null;
let lastAudit = null;
let editionTimer = null;
let editionReqId = 0;
let runToken = 0; // 每次发起对读或清空结果时自增，用于丢弃过期响应

const $ = (sel) => document.querySelector(sel);
const cplen = (s) => [...s].length;
const selKey = (type, index) => `${type}:${index}`;

/* ---------- 抄本录入行 ---------- */

function addWitnessRow(name = "", text = "") {
  const list = $("#witness-list");
  if (list.children.length >= MAX_WITNESSES) return;
  const row = document.createElement("div");
  row.className = "witness-row";
  row.innerHTML = `
    <div class="witness-head">
      <span class="w-index">抄本</span>
      <input class="w-name" type="text" maxlength="30" placeholder="抄本名称（如：甲本）">
      <span class="counter">0/${MAX_POINTS}</span>
      <button type="button" class="btn btn-small btn-remove">删除</button>
    </div>
    <textarea class="w-text" rows="2" placeholder="抄本文字，1～300 个 Unicode 码点；空白与标点照常比较。"></textarea>
  `;
  row.querySelector(".w-name").value = name;
  row.querySelector(".w-text").value = text;
  row.querySelector(".btn-remove").addEventListener("click", () => {
    if (list.children.length <= MIN_WITNESSES) {
      showError(`至少保留 ${MIN_WITNESSES} 份抄本。`);
      return;
    }
    row.remove();
    refreshWitnessRows();
    clearResults();
  });
  list.appendChild(row);
  refreshWitnessRows();
  // 新增抄本行即输入变化，旧结果立即失效
  clearResults();
}

function refreshWitnessRows() {
  const rows = [...document.querySelectorAll(".witness-row")];
  rows.forEach((row, i) => {
    row.querySelector(".w-index").textContent = `抄本 ${i + 1}`;
    row.querySelector(".counter").textContent =
      `${cplen(row.querySelector(".w-text").value)}/${MAX_POINTS}`;
    row.querySelector(".btn-remove").disabled = rows.length <= MIN_WITNESSES;
  });
  $("#btn-add-witness").disabled = rows.length >= MAX_WITNESSES;
}

/* ---------- 校验 ---------- */

function validateInput(base, witnesses, query = "") {
  if (!base) return "请输入底本文字。";
  if (cplen(base) > MAX_POINTS) return `底本长度不得超过 ${MAX_POINTS} 个码点。`;
  const names = new Set();
  for (const w of witnesses) {
    if (!w.name) return "每份抄本都必须填写名称。";
    if (names.has(w.name)) return `抄本名称重复：${w.name}`;
    names.add(w.name);
    if (!w.text) return `抄本「${w.name}」文字不能为空。`;
    if (cplen(w.text) > MAX_POINTS) {
      return `抄本「${w.name}」长度不得超过 ${MAX_POINTS} 个码点。`;
    }
  }
  if (query && cplen(query) > MAX_POINTS) {
    return `待鉴文本长度不得超过 ${MAX_POINTS} 个码点。`;
  }
  return null;
}

/* ---------- 结果清空（任一输入变化即生效） ---------- */

function clearResults() {
  runToken += 1;
  editionReqId += 1; // 作废在途的校定请求
  clearTimeout(editionTimer);
  lastResult = null;
  lastInput = null;
  selections = {};
  lastEdition = null;
  lastProv = null;
  lastRel = null;
  lastAudit = null;
  $("#results").classList.add("hidden");
  $("#provenance-error").classList.add("hidden");
  $("#provenance-error").textContent = "";
  $("#provenance-result").innerHTML = "";
  $("#relationships-error").classList.add("hidden");
  $("#relationships-error").textContent = "";
  $("#relationships-result").innerHTML = "";
  $("#audit-error").classList.add("hidden");
  $("#audit-error").textContent = "";
  $("#audit-result").innerHTML = "";
}

/* ---------- 对读请求 ---------- */

async function doCollate() {
  clearError();
  const base = $("#base-text").value;
  const witnesses = [...document.querySelectorAll(".witness-row")].map((row) => ({
    name: row.querySelector(".w-name").value.trim(),
    text: row.querySelector(".w-text").value,
  }));
  const query = $("#query-text").value;

  const err = validateInput(base, witnesses, query);
  if (err) {
    showError(err);
    return;
  }

  const token = ++runToken;
  let data;
  try {
    const resp = await fetch("/api/collate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ base, witnesses }),
    });
    data = await resp.json();
    if (!resp.ok) {
      showError(data.detail || "服务器校验失败。");
      return;
    }
  } catch (e) {
    showError("无法连接服务器，请确认后端已启动。");
    return;
  }
  if (token !== runToken) return; // 输入已变化，丢弃过期结果

  lastResult = data;
  lastInput = { base, witnesses };
  // 原文重新对读后，旧的校定选择全部失效，一并清空
  selections = {};
  lastEdition = null;
  lastProv = null;
  lastRel = null;
  lastAudit = null;
  renderResult(data);
  scheduleEdition();

  // 待鉴文本为空：仅对读，不做来源分段；否则同步请求并展示
  clearProvError();
  $("#provenance-result").innerHTML = "";
  if (!query) {
    renderProvPlaceholder("未填写待鉴文本，已跳过来源分段（仅完成对读）。");
  } else {
    renderProvPlaceholder("来源分段计算中……");
    requestProvenance(token, base, witnesses, query);
  }

  // 抄本异文距离树：与待鉴文本无关，随对读一并请求
  clearRelError();
  renderRelPlaceholder("抄本异文距离树计算中……");
  requestRelationships(token, base, witnesses);

  // 最优对齐路径审计：与待鉴文本无关，随对读一并请求
  clearAuditError();
  renderAuditPlaceholder("最优对齐路径审计计算中……");
  requestAlignmentAudit(token, base, witnesses);
}

async function requestProvenance(token, base, witnesses, query) {
  let data;
  try {
    const resp = await fetch("/api/provenance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ base, witnesses, query }),
    });
    data = await resp.json();
    if (!resp.ok) {
      if (token === runToken) showProvError(data.detail || "服务器校验失败。");
      return;
    }
  } catch (e) {
    if (token === runToken) showProvError("无法连接服务器，请确认后端已启动。");
    return;
  }
  if (token !== runToken) return; // 输入已变化，丢弃过期结果
  lastProv = data;
  renderProv(data.provenance);
}

/* ---------- 结果渲染 ---------- */

function renderResult(data) {
  $("#results").classList.remove("hidden");
  $("#summary").textContent =
    `底本 ${data.base.length} 字 · 抄本 ${data.witnesses.length} 份 · 差异位置 ${data.entries.length} 处`;

  renderTexts(data);

  const list = $("#entry-list");
  list.innerHTML = "";
  if (data.entries.length === 0) {
    const div = document.createElement("div");
    div.className = "empty";
    div.textContent = "各抄本与底本完全一致，无差异位置；校定稿即底本全文。";
    list.appendChild(div);
  } else {
    data.entries.forEach((entry) => list.appendChild(renderEntry(entry, data)));
  }
  $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderTexts(data) {
  const box = $("#text-panels");
  box.innerHTML = "";
  box.appendChild(buildTextPanel("底本", data.base.text, "base"));
  data.witnesses.forEach((w, i) =>
    box.appendChild(buildTextPanel(w.name, w.text, `w${i}`))
  );
}

function buildTextPanel(title, text, key) {
  const panel = document.createElement("div");
  panel.className = "text-panel";
  const head = document.createElement("div");
  head.className = "text-title";
  head.textContent = title;
  const body = document.createElement("div");
  body.className = "text-body";

  const chars = [...text]; // 按 Unicode 码点切分
  for (let i = 0; i <= chars.length; i++) {
    const gap = document.createElement("span");
    gap.className = "gap";
    gap.dataset.key = key;
    gap.dataset.g = String(i);
    body.appendChild(gap);
    if (i < chars.length) {
      const ch = document.createElement("span");
      ch.className = "ch";
      ch.dataset.key = key;
      ch.dataset.i = String(i);
      ch.textContent = chars[i];
      body.appendChild(ch);
    }
  }
  panel.append(head, body);
  return panel;
}

function readingLabel(reading, isCharEntry) {
  if (reading !== "") return `「${reading}」`;
  return isCharEntry ? "（缺字）" : "（无插入）";
}

/* ---------- 差异条目（含校定读法单选） ---------- */

function renderEntry(entry, data) {
  const isCharEntry = entry.coordinate.type === "char";
  const div = document.createElement("div");
  div.className = "entry";
  div.dataset.id = String(entry.id);

  const head = document.createElement("div");
  head.className = "entry-head";
  const coord = document.createElement("span");
  coord.className = "coord";
  coord.textContent = entry.coordinate.label;
  const kinds = document.createElement("span");
  kinds.className = "kinds";
  kinds.textContent = entry.diff_kinds.map((k) => KIND_LABEL[k]).join(" / ");
  head.append(coord, kinds);

  const options = document.createElement("div");
  options.className = "reading-options";
  // 点选读法不应改变原文对照的高亮状态
  options.addEventListener("click", (ev) => ev.stopPropagation());

  const radioName = `opt-${entry.id}`;
  const baseGroup = entry.groups.find((g) => g.reading === entry.base_reading);
  const baseSupporters = ["底本", ...((baseGroup && baseGroup.witnesses) || [])];

  options.appendChild(
    buildOption(radioName, entry, entry.base_reading, "底本", baseSupporters, true)
  );

  // 底本读法之外的抄本读法：同读法的抄本已在 groups 中合并
  const otherGroups = entry.groups
    .filter((g) => g.reading !== entry.base_reading)
    .sort((a, b) => Number(a.kind === "match") - Number(b.kind === "match"));
  for (const g of otherGroups) {
    options.appendChild(
      buildOption(radioName, entry, g.reading, KIND_LABEL[g.kind] || g.kind,
                  g.witnesses, false)
    );
  }

  div.append(head, options);
  div.addEventListener("click", () => selectEntry(entry, data, div));
  return div;
}

function buildOption(radioName, entry, reading, sourceLabel, supporters, isBase) {
  const isCharEntry = entry.coordinate.type === "char";
  const label = document.createElement("label");
  label.className = "reading-option";

  const radio = document.createElement("input");
  radio.type = "radio";
  radio.name = radioName;
  radio.value = reading;
  // 默认取底本；其余读法仅在已有选择时选中
  radio.checked = isBase
    ? !(selKey(entry.coordinate.type, entry.coordinate.index) in selections)
    : selections[selKey(entry.coordinate.type, entry.coordinate.index)] === reading;

  const text = document.createElement("span");
  text.className = "option-text";
  text.textContent =
    `${sourceLabel}：${readingLabel(reading, isCharEntry)} —— ${supporters.join("、")}`;

  label.append(radio, text);
  radio.addEventListener("change", () => {
    const key = selKey(entry.coordinate.type, entry.coordinate.index);
    if (isBase) {
      // 底本为默认读法，无需记录
      delete selections[key];
    } else {
      selections[key] = reading;
    }
    scheduleEdition();
  });
  return label;
}

/* ---------- 校定稿 ---------- */

function scheduleEdition() {
  clearTimeout(editionTimer);
  editionTimer = setTimeout(updateEdition, 120);
}

async function updateEdition() {
  if (!lastInput) return;
  const reqId = ++editionReqId;
  const body = {
    base: lastInput.base,
    witnesses: lastInput.witnesses,
    selections: Object.entries(selections).map(([key, reading]) => {
      const [type, index] = key.split(":");
      return { type, index: Number(index), reading };
    }),
  };

  let data;
  try {
    const resp = await fetch("/api/edition", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    data = await resp.json();
    if (!resp.ok) {
      if (reqId === editionReqId) showEditionError(data.detail || "校定请求被服务器拒绝。");
      return;
    }
  } catch (e) {
    if (reqId === editionReqId) showEditionError("无法连接服务器，请确认后端已启动。");
    return;
  }
  if (reqId !== editionReqId) return; // 已有更新的选择，丢弃过期响应
  clearEditionError();
  lastEdition = data;
  renderEdition(data);
}

function renderEdition(data) {
  const ed = data.edition;
  const chosenCount = data.selections.length;
  $("#edition-summary").textContent =
    `校定文本 ${ed.length} 个 Unicode 码点 · 非默认选择 ${chosenCount} 处`;

  const box = $("#edition-preview");
  box.innerHTML = "";
  for (const c of ed.coordinates) {
    const empty = c.span[0] === c.span[1];
    // 无插入的间隙不占篇幅、不显示；缺字（字符位空读法）以零长标记示出
    if (empty && c.coordinate.type === "gap") continue;
    const span = document.createElement("span");
    span.className = empty ? "ed-read ed-empty" : "ed-read";
    span.textContent = c.reading;
    span.title =
      `${c.coordinate.label}：${readingLabel(c.reading, c.coordinate.type === "char")}
支持：${c.supporters.join("、")}
成稿区间：[${c.span[0]}, ${c.span[1]})`;
    box.appendChild(span);
  }
}

function showEditionError(msg) {
  const el = $("#edition-error");
  el.textContent = msg;
  el.classList.remove("hidden");
}

function clearEditionError() {
  const el = $("#edition-error");
  el.textContent = "";
  el.classList.add("hidden");
}

function downloadEdition() {
  if (!lastEdition) return;
  const blob = new Blob([lastEdition.edition.text], {
    type: "text/plain;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "jiaoding.txt";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/* ---------- 待鉴文本来源分段 ---------- */

function showProvError(msg) {
  const el = $("#provenance-error");
  el.textContent = msg;
  el.classList.remove("hidden");
  $("#provenance-result").innerHTML = "";
}

function clearProvError() {
  const el = $("#provenance-error");
  el.textContent = "";
  el.classList.add("hidden");
}

function renderProvPlaceholder(msg) {
  const box = $("#provenance-result");
  box.innerHTML = "";
  const div = document.createElement("div");
  div.className = "empty";
  div.textContent = msg;
  box.appendChild(div);
}

function segRange(seg) {
  return seg.start.label === seg.end.label
    ? seg.start.label
    : `${seg.start.label}～${seg.end.label}`;
}

function renderProv(prov) {
  clearProvError();
  const box = $("#provenance-result");
  box.innerHTML = "";

  if (!prov.decidable) {
    const div = document.createElement("div");
    div.className = "empty";
    div.textContent = "无法判定：各抄本读法在所有坐标上完全相同，没有鉴别坐标。";
    box.appendChild(div);
    return;
  }

  const bar = document.createElement("div");
  bar.className = "prov-summary";
  bar.textContent =
    `鉴别坐标 ${prov.discriminative_count} 处 · 总分 ${prov.score} 分` +
    `（失配 ${prov.mismatches}，换源 ${prov.switches} 次）`;
  box.appendChild(bar);

  const segHead = document.createElement("h4");
  segHead.className = "prov-head";
  segHead.textContent = "连续同源区段";
  box.appendChild(segHead);

  const segList = document.createElement("ol");
  segList.className = "prov-segments";
  for (const seg of prov.segments) {
    const li = document.createElement("li");
    li.className = "prov-segment";
    const span = document.createElement("span");
    span.className = "seg-range";
    span.textContent = segRange(seg);
    const wit = document.createElement("span");
    wit.className = "seg-witness";
    wit.textContent = seg.witness;
    const mis = document.createElement("span");
    mis.className = "seg-mismatch";
    mis.textContent = `失配 ${seg.mismatches}`;
    li.append(span, wit, mis);
    segList.appendChild(li);
  }
  box.appendChild(segList);

  const pathHead = document.createElement("h4");
  pathHead.className = "prov-head";
  pathHead.textContent = "鉴别坐标明细";
  box.appendChild(pathHead);

  const table = document.createElement("table");
  table.className = "prov-path";
  table.innerHTML = `
    <thead><tr>
      <th>坐标</th><th>待鉴读法</th><th>来源抄本</th><th>计分</th><th>各抄本读法</th>
    </tr></thead>`;
  const tbody = document.createElement("tbody");
  for (const p of prov.path) {
    const tr = document.createElement("tr");
    const tdCoord = document.createElement("td");
    tdCoord.textContent = p.coordinate.label;
    const tdQuery = document.createElement("td");
    tdQuery.textContent = readingLabel(p.query_reading, p.coordinate.type === "char");
    const tdSrc = document.createElement("td");
    tdSrc.textContent = p.source;
    const tdScore = document.createElement("td");
    tdScore.textContent = p.mismatch ? "失配 1" : "相同 0";
    const tdReads = document.createElement("td");
    tdReads.textContent = p.witness_readings
      .map((wr) => {
        const label = readingLabel(wr.reading, p.coordinate.type === "char");
        return wr.matches_query ? `${wr.name}＝${label}` : `${wr.name}≠${label}`;
      })
      .join("；");
    tr.append(tdCoord, tdQuery, tdSrc, tdScore, tdReads);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  box.appendChild(table);
}

/* ---------- 抄本异文距离树 ---------- */

async function requestRelationships(token, base, witnesses) {
  let data;
  try {
    const resp = await fetch("/api/relationships", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ base, witnesses }),
    });
    data = await resp.json();
    if (!resp.ok) {
      if (token === runToken) showRelError(data.detail || "服务器校验失败。");
      return;
    }
  } catch (e) {
    if (token === runToken) showRelError("无法连接服务器，请确认后端已启动。");
    return;
  }
  if (token !== runToken) return; // 输入已变化，丢弃过期结果
  lastRel = data;
  renderRel(data.relationships);
}

function showRelError(msg) {
  const el = $("#relationships-error");
  el.textContent = msg;
  el.classList.remove("hidden");
  $("#relationships-result").innerHTML = "";
}

function clearRelError() {
  const el = $("#relationships-error");
  el.textContent = "";
  el.classList.add("hidden");
}

function renderRelPlaceholder(msg) {
  const box = $("#relationships-result");
  box.innerHTML = "";
  const div = document.createElement("div");
  div.className = "empty";
  div.textContent = msg;
  box.appendChild(div);
}

function endpointLabel(ep) {
  return `抄本 ${ep.index} ${ep.name}`;
}

function renderRel(rel) {
  clearRelError();
  const box = $("#relationships-result");
  box.innerHTML = "";

  if (!rel.decidable) {
    const div = document.createElement("div");
    div.className = "empty";
    div.textContent = "无法比较：各抄本读法在所有坐标上完全相同，没有鉴别坐标，不生成树。";
    box.appendChild(div);
    return;
  }

  const bar = document.createElement("div");
  bar.className = "prov-summary";
  bar.textContent =
    `鉴别坐标 ${rel.discriminative_count} 处 · 树边 ${rel.tree.length} 条` +
    `（连接全部 ${rel.tree.length + 1} 份抄本）`;
  box.appendChild(bar);

  const list = document.createElement("ol");
  list.className = "rel-edges";
  for (const edge of rel.tree) {
    const li = document.createElement("li");
    li.className = "rel-edge";

    const ratio = ((edge.retained / edge.total) * 100).toFixed(1);
    const head = document.createElement("div");
    head.className = "rel-edge-head";
    const span = (cls, text) => {
      const el = document.createElement("span");
      el.className = cls;
      el.textContent = text;
      return el;
    };
    head.appendChild(span("rel-endpoints",
      `${endpointLabel(edge.a)} — ${endpointLabel(edge.b)}`));
    head.appendChild(span("rel-distance", `距离 ${edge.distance}`));
    head.appendChild(span("rel-retained",
      `保留 ${edge.retained}/${edge.total}（${ratio}%）`));

    const detail = document.createElement("div");
    detail.className = "rel-edge-detail hidden";
    const detailHead = document.createElement("div");
    detailHead.className = "rel-detail-head";
    detailHead.textContent = `两端读法不同的坐标（共 ${edge.differences.length} 处）`;
    detail.appendChild(detailHead);

    if (edge.differences.length === 0) {
      const none = document.createElement("div");
      none.className = "empty";
      none.textContent = "两端在全部鉴别坐标上读法相同（距离 0）。";
      detail.appendChild(none);
    } else {
      const table = document.createElement("table");
      table.className = "prov-path rel-table";
      const thead = document.createElement("thead");
      const headRow = document.createElement("tr");
      for (const text of ["坐标", endpointLabel(edge.a), endpointLabel(edge.b)]) {
        const th = document.createElement("th");
        th.textContent = text;
        headRow.appendChild(th);
      }
      thead.appendChild(headRow);
      table.appendChild(thead);
      const tbody = document.createElement("tbody");
      for (const d of edge.differences) {
        const tr = document.createElement("tr");
        const tdCoord = document.createElement("td");
        tdCoord.textContent = d.coordinate.label;
        const tdA = document.createElement("td");
        tdA.textContent = readingLabel(d.a_reading, d.coordinate.type === "char");
        const tdB = document.createElement("td");
        tdB.textContent = readingLabel(d.b_reading, d.coordinate.type === "char");
        tr.append(tdCoord, tdA, tdB);
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      detail.appendChild(table);
    }

    head.addEventListener("click", () => {
      li.classList.toggle("active");
      detail.classList.toggle("hidden");
    });
    li.append(head, detail);
    list.appendChild(li);
  }
  box.appendChild(list);
}

/* ---------- 最优对齐路径审计 ---------- */

async function requestAlignmentAudit(token, base, witnesses) {
  let data;
  try {
    const resp = await fetch("/api/alignment-audit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ base, witnesses }),
    });
    data = await resp.json();
    if (!resp.ok) {
      if (token === runToken) showAuditError(data.detail || "服务器校验失败。");
      return;
    }
  } catch (e) {
    if (token === runToken) showAuditError("无法连接服务器，请确认后端已启动。");
    return;
  }
  if (token !== runToken) return; // 输入已变化，丢弃过期结果
  lastAudit = data;
  renderAudit(data.audit);
}

function showAuditError(msg) {
  const el = $("#audit-error");
  el.textContent = msg;
  el.classList.remove("hidden");
  $("#audit-result").innerHTML = "";
}

function clearAuditError() {
  const el = $("#audit-error");
  el.textContent = "";
  el.classList.add("hidden");
}

function renderAuditPlaceholder(msg) {
  const box = $("#audit-result");
  box.innerHTML = "";
  const div = document.createElement("div");
  div.className = "empty";
  div.textContent = msg;
  box.appendChild(div);
}

function auditCellText(r, isChar) {
  const span = `[${r.span[0]}, ${r.span[1]})`;
  const label = readingLabel(r.reading, isChar);
  // 空读法（缺字／无插入）的标签已含类别，不再重复操作名
  return r.reading === "" ? `${label} ${span}` : `${KIND_LABEL[r.kind]}${label} ${span}`;
}

function renderAudit(audit) {
  clearAuditError();
  const box = $("#audit-result");
  box.innerHTML = "";

  const list = document.createElement("ol");
  list.className = "rel-edges";
  for (const w of audit.witnesses) {
    const li = document.createElement("li");
    li.className = "rel-edge";

    const head = document.createElement("div");
    head.className = "rel-edge-head";
    const span = (cls, text) => {
      const el = document.createElement("span");
      el.className = cls;
      el.textContent = text;
      return el;
    };
    head.appendChild(span("rel-endpoints", w.name));
    head.appendChild(span("audit-count", `最优路径 ${w.path_count} 条`));

    if (!w.alternative) {
      head.classList.add("static");
      head.appendChild(span("rel-retained", "路径唯一，无替代路径"));
      li.appendChild(head);
      list.appendChild(li);
      continue;
    }

    const diffs = w.alternative.differences;
    head.appendChild(span("rel-retained",
      `次小路径造成 ${diffs.length} 处坐标差别（点击展开）`));

    const detail = document.createElement("div");
    detail.className = "rel-edge-detail hidden";
    const detailHead = document.createElement("div");
    detailHead.className = "rel-detail-head";
    detailHead.textContent =
      `首条路径与严格次小路径有差别的坐标（共 ${diffs.length} 处）`;
    detail.appendChild(detailHead);

    const table = document.createElement("table");
    table.className = "prov-path rel-table";
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    for (const text of ["坐标", "首条路径（现行取舍）", "严格次小路径"]) {
      const th = document.createElement("th");
      th.textContent = text;
      headRow.appendChild(th);
    }
    thead.appendChild(headRow);
    table.appendChild(thead);
    const tbody = document.createElement("tbody");
    for (const d of diffs) {
      const isChar = d.coordinate.type === "char";
      const tr = document.createElement("tr");
      const tdCoord = document.createElement("td");
      tdCoord.textContent = d.coordinate.label;
      const tdFirst = document.createElement("td");
      tdFirst.textContent = auditCellText(d.first, isChar);
      const tdSecond = document.createElement("td");
      tdSecond.textContent = auditCellText(d.second, isChar);
      tr.append(tdCoord, tdFirst, tdSecond);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    detail.appendChild(table);

    head.addEventListener("click", () => {
      li.classList.toggle("active");
      detail.classList.toggle("hidden");
    });
    li.append(head, detail);
    list.appendChild(li);
  }
  box.appendChild(list);
}

/* ---------- 高亮 ---------- */

function selectEntry(entry, data, div) {
  document.querySelectorAll(".entry.active").forEach((el) =>
    el.classList.remove("active")
  );
  document.querySelectorAll(".hl").forEach((el) => el.classList.remove("hl"));

  div.classList.add("active");
  applySpans("base", entry.spans.base);
  data.witnesses.forEach((w, i) => {
    const spans = entry.spans.witnesses[w.name];
    if (spans) applySpans(`w${i}`, spans);
  });

  const first = document.querySelector(".hl");
  if (first) first.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
}

function applySpans(key, spans) {
  for (const [start, end] of spans) {
    if (start === end) {
      const el = document.querySelector(
        `.gap[data-key="${key}"][data-g="${start}"]`
      );
      if (el) el.classList.add("hl");
    } else {
      for (let i = start; i < end; i++) {
        const el = document.querySelector(
          `.ch[data-key="${key}"][data-i="${i}"]`
        );
        if (el) el.classList.add("hl");
      }
    }
  }
}

/* ---------- JSON 下载 ---------- */

function downloadJson() {
  if (!lastResult) return;
  const d = lastResult;
  const payload = {
    tool: "异文对读台",
    generated_at: new Date().toISOString(),
    base: d.base,
    witnesses: d.witnesses,
    entries: d.entries.map((e) => ({
      coordinate: e.coordinate,
      base_reading: e.base_reading,
      groups: e.groups.map((g) => ({
        reading: g.reading,
        kind: g.kind,
        kind_label: KIND_LABEL[g.kind] || g.kind,
        witnesses: g.witnesses,
      })),
    })),
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "yiwen-collation.json";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/* ---------- 杂项 ---------- */

function showError(msg) {
  const el = $("#error");
  el.textContent = msg;
  el.classList.remove("hidden");
}

function clearError() {
  const el = $("#error");
  el.textContent = "";
  el.classList.add("hidden");
}

function fillExample() {
  clearError();
  $("#base-text").value = EXAMPLE.base;
  $("#witness-list").innerHTML = "";
  EXAMPLE.witnesses.forEach((w) => addWitnessRow(w.name, w.text));
  $("#query-text").value = EXAMPLE.query;
  refreshWitnessRows();
  $("#query-counter").textContent = `${cplen(EXAMPLE.query)}/${MAX_POINTS}`;
  clearResults();
}

function init() {
  // 任一输入（底本、抄本名／文、待鉴文本）变化，立即清空旧结果，
  // 并作废在途请求；计数器一并实时更新。
  $("#input-section").addEventListener("input", (ev) => {
    const t = ev.target;
    if (t.id === "base-text") {
      $("#base-counter").textContent = `${cplen(t.value)}/${MAX_POINTS}`;
    } else if (t.id === "query-text") {
      $("#query-counter").textContent = `${cplen(t.value)}/${MAX_POINTS}`;
    } else if (t.classList.contains("w-text")) {
      refreshWitnessRows();
    }
    clearResults();
  });
  $("#btn-add-witness").addEventListener("click", () => addWitnessRow());
  $("#btn-example").addEventListener("click", fillExample);
  $("#btn-collate").addEventListener("click", doCollate);
  $("#btn-download").addEventListener("click", downloadJson);
  $("#btn-download-edition").addEventListener("click", downloadEdition);

  addWitnessRow();
  addWitnessRow();
  $("#base-counter").textContent = `0/${MAX_POINTS}`;
  $("#query-counter").textContent = `0/${MAX_POINTS}`;
}

document.addEventListener("DOMContentLoaded", init);
