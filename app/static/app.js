/* 陶片拼接工作台前端（原生 JS + Canvas，无构建步骤） */
"use strict";

const state = {
  data: null,
  editor: { contour: [], markers: [], mode: "contour" },
  world: { scale: 4, ox: 80, oy: 600, dragging: false, px: 0, py: 0 },
  preview: null,          // 当前预览结果
  previewRelId: null,
};

const COLORS = ["#c1572f", "#2f6d8f", "#2f8f57", "#8f5b2f", "#6b4a8f",
                "#8f8a2f", "#2f4a8f", "#8f2f6b"];
const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------
async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = json.error || { message: `请求失败（${res.status}）`, code: "HTTP_ERROR" };
    throw Object.assign(new Error(err.message), { code: err.code, details: err.details });
  }
  return json;
}

function toast(msg, kind = "info", ms = 4000) {
  const box = $("toast");
  const el = document.createElement("div");
  el.className = `toast-item ${kind}`;
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

// ---------------------------------------------------------------------------
// 状态
// ---------------------------------------------------------------------------
async function refresh() {
  state.data = await api("/api/state");
  $("stateVer").textContent = state.data.state_version;
  $("joinTol").textContent = state.data.tolerance_mm;
  renderWorld();
  renderSelectors();
  renderRelations();
}

function fragById(id) {
  return state.data.fragments.find((f) => f.id === id);
}
function fragColor(f) { return COLORS[(f.id - 1) % COLORS.length]; }
function relById(id) {
  return state.data.relations.find((r) => r.id === id);
}

// ---------------------------------------------------------------------------
// Tab 切换
// ---------------------------------------------------------------------------
document.querySelectorAll(".tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    document.querySelectorAll(".tab").forEach((t) => (t.hidden = true));
    $(`tab-${btn.dataset.tab}`).hidden = false;
  });
});

// ---------------------------------------------------------------------------
// 编辑器
// ---------------------------------------------------------------------------
const eCanvas = $("editorCanvas");
const eCtx = eCanvas.getContext("2d");
const VIEW = { scale: 5, ox: 40, oy: 380 }; // 毫米 -> 像素（y 轴翻转）

function edToPx(x, y) {
  return [VIEW.ox + x * VIEW.scale, VIEW.oy - y * VIEW.scale];
}
function edToMm(px, py) {
  return [(px - VIEW.ox) / VIEW.scale, (VIEW.oy - py) / VIEW.scale];
}

function syncTextarea() {
  $("contourText").value = state.editor.contour
    .map(([x, y]) => `${round3(x)}, ${round3(y)}`).join("\n");
}
function round3(v) { return Math.round(v * 1000) / 1000; }

$("contourText").addEventListener("change", () => {
  const pts = [];
  for (const line of $("contourText").value.split("\n")) {
    const t = line.trim();
    if (!t) continue;
    const parts = t.split(/[,，\s]+/).filter(Boolean);
    if (parts.length === 2 && parts.every((p) => !isNaN(parseFloat(p)))) {
      pts.push([parseFloat(parts[0]), parseFloat(parts[1])]);
    }
  }
  state.editor.contour = pts;
  drawEditor();
});

$("modeContour").addEventListener("click", () => setMode("contour"));
$("modeMarker").addEventListener("click", () => setMode("marker"));
function setMode(mode) {
  state.editor.mode = mode;
  $("modeContour").classList.toggle("active", mode === "contour");
  $("modeMarker").classList.toggle("active", mode === "marker");
  eCanvas.style.cursor = mode === "marker" ? "copy" : "crosshair";
}

$("undoPoint").addEventListener("click", () => {
  if (state.editor.mode === "contour") state.editor.contour.pop();
  else state.editor.markers.pop();
  syncTextarea();
  drawEditor();
});
$("clearEditor").addEventListener("click", () => {
  state.editor.contour = [];
  state.editor.markers = [];
  syncTextarea();
  drawEditor();
  edMsg("", true);
});

function nearestEdgePoint(p, poly) {
  if (poly.length < 2) return null;
  let best = null;
  let bestD = Infinity;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i], b = poly[(i + 1) % poly.length];
    const abx = b[0] - a[0], aby = b[1] - a[1];
    const L2 = abx * abx + aby * aby || 1e-9;
    let t = ((p[0] - a[0]) * abx + (p[1] - a[1]) * aby) / L2;
    t = Math.max(0, Math.min(1, t));
    const q = [a[0] + t * abx, a[1] + t * aby];
    const d = Math.hypot(q[0] - p[0], q[1] - p[1]);
    if (d < bestD) { bestD = d; best = q; }
  }
  return { point: best, dist: bestD };
}

eCanvas.addEventListener("click", (ev) => {
  const rect = eCanvas.getBoundingClientRect();
  const px = (ev.clientX - rect.left) * (eCanvas.width / rect.width);
  const py = (ev.clientY - rect.top) * (eCanvas.height / rect.height);
  const m = edToMm(px, py);
  if (state.editor.mode === "contour") {
    state.editor.contour.push([round3(m[0]), round3(m[1])]);
    syncTextarea();
    edMsg(`已添加顶点 (${round3(m[0])}, ${round3(m[1])})，共 ${state.editor.contour.length} 点`, true);
  } else {
    const snap = nearestEdgePoint(m, state.editor.contour);
    if (!snap) { edMsg("请先录入至少 2 个轮廓顶点"); return; }
    const label = $("markerLabel").value.trim() || `M${state.editor.markers.length + 1}`;
    if (state.editor.markers.some((mk) => mk.label === label)) {
      edMsg(`标记名“${label}”已存在`); return;
    }
    state.editor.markers.push({ label, x: round3(snap.point[0]), y: round3(snap.point[1]) });
    edMsg(`已在边缘添加标记“${label}” (${round3(snap.point[0])}, ${round3(snap.point[1])})`, true);
  }
  drawEditor();
});

function edMsg(text, ok = false) {
  const el = $("editorMsg");
  el.textContent = text;
  el.className = "msg" + (ok ? " ok" : "");
}

function drawEditor() {
  const ctx = eCtx;
  ctx.clearRect(0, 0, eCanvas.width, eCanvas.height);
  drawGrid(ctx, eCanvas.width, eCanvas.height, VIEW, true);
  const poly = state.editor.contour;

  if (poly.length >= 3) {
    ctx.beginPath();
    poly.forEach((p, i) => {
      const [x, y] = edToPx(p[0], p[1]);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.closePath();
    ctx.fillStyle = "rgba(193,87,47,0.15)";
    ctx.fill();
    ctx.strokeStyle = "#c1572f";
    ctx.lineWidth = 1.6;
    ctx.stroke();
  } else if (poly.length === 2) {
    drawLine(ctx, poly[0], poly[1], "#c1572f");
  }
  poly.forEach((p, i) => {
    const [x, y] = edToPx(p[0], p[1]);
    ctx.fillStyle = "#c1572f";
    ctx.beginPath(); ctx.arc(x, y, 3.5, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#6b6255"; ctx.font = "10px sans-serif";
    ctx.fillText(String(i + 1), x + 5, y - 5);
  });
  state.editor.markers.forEach((mk) => {
    const [x, y] = edToPx(mk.x, mk.y);
    ctx.fillStyle = "#e8b04b";
    ctx.strokeStyle = "#7a5a33";
    ctx.beginPath();
    ctx.moveTo(x, y - 6); ctx.lineTo(x + 6, y); ctx.lineTo(x, y + 6);
    ctx.lineTo(x - 6, y); ctx.closePath();
    ctx.fill(); ctx.stroke();
    ctx.fillStyle = "#7a5a33"; ctx.font = "bold 11px sans-serif";
    ctx.fillText(mk.label, x + 8, y - 6);
  });
}

function drawLine(ctx, a, b, color) {
  const [x1, y1] = edToPx(a[0], a[1]);
  const [x2, y2] = edToPx(b[0], b[1]);
  ctx.strokeStyle = color; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
}

function drawGrid(ctx, w, h, v, editorStyle) {
  ctx.save();
  ctx.strokeStyle = "#eae3d4";
  ctx.fillStyle = "#b3a892";
  ctx.font = "9px sans-serif";
  ctx.lineWidth = 1;
  const step = 10; // 10 mm 网格
  for (let mm = 0; ; mm += step) {
    const px = v.ox + mm * v.scale;
    if (px > w) break;
    ctx.beginPath(); ctx.moveTo(px, 0); ctx.lineTo(px, h); ctx.stroke();
    if (mm > 0) ctx.fillText(`${mm}`, px + 1, h - 4);
  }
  for (let mm = 0; ; mm += step) {
    let py;
    if (editorStyle) py = v.oy - mm * v.scale;
    else py = v.oy - mm * v.scale;
    if (py < 0) break;
    ctx.beginPath(); ctx.moveTo(0, py); ctx.lineTo(w, py); ctx.stroke();
    if (mm > 0) ctx.fillText(`${mm}`, 4, py - 2);
  }
  ctx.restore();
}

$("submitFrag").addEventListener("click", async () => {
  const name = $("fragName").value.trim();
  const contour = state.editor.contour;
  const markers = state.editor.markers;
  if (!name) return edMsg("请填写陶片名称");
  if (contour.length < 3) return edMsg("闭合轮廓至少需要 3 个顶点");
  try {
    const r = await api("/api/fragments", {
      method: "POST",
      body: { name, contour_mm: contour, markers },
    });
    edMsg(`陶片“${name}”已录入（id=${r.id}）`, true);
    toast(`陶片“${name}”录入成功`, "success");
    state.editor.contour = [];
    state.editor.markers = [];
    $("fragName").value = "";
    $("markerLabel").value = "";
    syncTextarea();
    drawEditor();
    await refresh();
  } catch (e) {
    edMsg(e.message);
    toast(e.message, "error", 6000);
  }
});

// ---------------------------------------------------------------------------
// 世界视图
// ---------------------------------------------------------------------------
const wCanvas = $("worldCanvas");
const wCtx = wCanvas.getContext("2d");

function wToPx(x, y) {
  const w = state.world;
  return [w.ox + x * w.scale, w.oy - y * w.scale];
}

wCanvas.addEventListener("wheel", (ev) => {
  ev.preventDefault();
  const rect = wCanvas.getBoundingClientRect();
  const mx = (ev.clientX - rect.left) * (wCanvas.width / rect.width);
  const my = (ev.clientY - rect.top) * (wCanvas.height / rect.height);
  const w = state.world;
  const mmx = (mx - w.ox) / w.scale, mmy = (w.oy - my) / w.scale;
  const factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
  w.scale = Math.min(40, Math.max(0.5, w.scale * factor));
  w.ox = mx - mmx * w.scale;
  w.oy = my + mmy * w.scale;
  renderWorld();
}, { passive: false });

wCanvas.addEventListener("mousedown", (ev) => {
  state.world.dragging = true;
  state.world.px = ev.clientX;
  state.world.py = ev.clientY;
});
window.addEventListener("mousemove", (ev) => {
  if (!state.world.dragging) return;
  const rect = wCanvas.getBoundingClientRect();
  const kx = wCanvas.width / rect.width, ky = wCanvas.height / rect.height;
  state.world.ox += (ev.clientX - state.world.px) * kx;
  state.world.oy += (ev.clientY - state.world.py) * ky;
  state.world.px = ev.clientX;
  state.world.py = ev.clientY;
  renderWorld();
});
window.addEventListener("mouseup", () => (state.world.dragging = false));

function drawPoly(ctx, poly, color, fillAlpha = 0.12, dashed = false, lineWidth = 1.8) {
  if (!poly.length) return;
  ctx.save();
  ctx.beginPath();
  poly.forEach((p, i) => {
    const [x, y] = wToPx(p[0], p[1]);
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.closePath();
  ctx.fillStyle = hexAlpha(color, fillAlpha);
  ctx.fill();
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  if (dashed) ctx.setLineDash([7, 5]);
  ctx.stroke();
  ctx.restore();
}

function hexAlpha(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

function renderWorld() {
  if (!state.data) return;
  const ctx = wCtx;
  ctx.clearRect(0, 0, wCanvas.width, wCanvas.height);
  drawGrid(ctx, wCanvas.width, wCanvas.height, state.world, false);

  // 已采纳关系：以线段连接对应世界标记
  ctx.lineWidth = 1.2;
  for (const rel of state.data.relations) {
    if (rel.status !== "adopted") continue;
    ctx.strokeStyle = "rgba(47,109,79,0.55)";
    ctx.setLineDash([3, 3]);
    for (const [maId, mbId] of rel.pairs) {
      const pa = worldMarkerPos(maId), pb = worldMarkerPos(mbId);
      if (!pa || !pb) continue;
      const [x1, y1] = wToPx(pa[0], pa[1]);
      const [x2, y2] = wToPx(pb[0], pb[1]);
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    }
    ctx.setLineDash([]);
  }

  // 陶片实体
  for (const f of state.data.fragments) {
    drawPoly(ctx, f.world_contour_mm, fragColor(f), 0.14);
    const [cx, cy] = centroid(f.world_contour_mm);
    const [px, py] = wToPx(cx, cy);
    ctx.fillStyle = fragColor(f);
    ctx.font = "bold 13px sans-serif";
    ctx.fillText(`${f.name} #${f.id}`, px - 24, py - 8);
    ctx.font = "11px sans-serif";
    ctx.fillStyle = "#5a5043";
    const ang = normAngle(f.pose.angle_deg);
    ctx.fillText(
      `组G${f.group_id}  (${round3(f.pose.x_mm)}, ${round3(f.pose.y_mm)}) mm  θ=${round3(ang)}°`,
      px - 30, py + 8);
    // 标记
    for (const m of f.markers) {
      const [mx, my] = wToPx(m.world_x_mm, worldMarkerY(m));
      drawMarkerDiamond(ctx, mx, my, m.label, "#e8b04b", "#7a5a33");
    }
  }

  // 预览叠加：被移动组虚线显示目标位姿
  if (state.preview && state.preview.valid !== undefined) {
    drawPreviewOverlay();
  }
}

function worldMarkerY(m) { return m.world_y_mm; }

function worldMarkerPos(markerId) {
  for (const f of state.data.fragments) {
    const m = f.markers.find((x) => x.id === markerId);
    if (m) return [m.world_x_mm, m.world_y_mm];
  }
  return null;
}

function centroid(poly) {
  let x = 0, y = 0;
  poly.forEach((p) => { x += p[0]; y += p[1]; });
  return [x / poly.length, y / poly.length];
}
function normAngle(deg) {
  let d = deg % 360;
  if (d > 180) d -= 360;
  if (d <= -180) d += 360;
  return d;
}

function drawMarkerDiamond(ctx, x, y, label, fill, stroke) {
  ctx.save();
  ctx.fillStyle = fill; ctx.strokeStyle = stroke; ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.moveTo(x, y - 6); ctx.lineTo(x + 6, y); ctx.lineTo(x, y + 6);
  ctx.lineTo(x - 6, y); ctx.closePath();
  ctx.fill(); ctx.stroke();
  ctx.fillStyle = stroke; ctx.font = "bold 10px sans-serif";
  ctx.fillText(label, x + 8, y - 7);
  ctx.restore();
}

// 预览叠加：依据 moving_fragments 的 proposed 位姿重算世界多边形
function drawPreviewOverlay() {
  const pv = state.preview;
  const ctx = wCtx;
  for (const mv of pv.moving_fragments) {
    const f = fragById(mv.id);
    if (!f) continue;
    const th = mv.proposed.angle_deg * Math.PI / 180;
    const poly = f.contour_mm.map(([x, y]) => {
      const c = Math.cos(th), s = Math.sin(th);
      return [mv.proposed.x_mm + c * x - s * y,
              mv.proposed.y_mm + s * x + c * y];
    });
    drawPoly(ctx, poly, "#7a3aa2", pv.valid ? 0.1 : 0.18, true, 2);
  }
  // 对应点连线（目标位置）
  ctx.strokeStyle = pv.valid ? "rgba(47,109,79,0.9)" : "rgba(162,58,47,0.9)";
  ctx.setLineDash([]);
  ctx.lineWidth = 1.6;
  for (const p of pv.pairs) {
    const pa = worldMarkerPos(p.marker_a);
    const pb = movedMarkerPos(p.marker_b);
    if (!pa || !pb) continue;
    const [x1, y1] = wToPx(pa[0], pa[1]);
    const [x2, y2] = wToPx(pb[0], pb[1]);
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
  }
}

// 预览中 B 侧标记的目标世界坐标（应用预览变换）
function movedMarkerPos(markerId) {
  const pv = state.preview;
  if (!pv) return worldMarkerPos(markerId);
  const f = state.data.fragments.find((fr) =>
    fr.markers.some((m) => m.id === markerId));
  if (!f) return null;
  const mv = pv.moving_fragments.find((x) => x.id === f.id);
  const m = f.markers.find((x) => x.id === markerId);
  if (!mv) return [m.world_x_mm, m.world_y_mm]; // 闭环：不动
  const th = mv.proposed.angle_deg * Math.PI / 180;
  const c = Math.cos(th), s = Math.sin(th);
  return [mv.proposed.x_mm + c * m.x_mm - s * m.y_mm,
          mv.proposed.y_mm + s * m.x_mm + c * m.y_mm];
}

// ---------------------------------------------------------------------------
// 候选拼接表单
// ---------------------------------------------------------------------------
function renderSelectors() {
  const selA = $("selA"), selB = $("selB");
  const curA = selA.value, curB = selB.value;
  selA.innerHTML = ""; selB.innerHTML = "";
  for (const f of state.data.fragments) {
    selA.add(new Option(`${f.name} #${f.id}（组G${f.group_id}）`, f.id));
    selB.add(new Option(`${f.name} #${f.id}（组G${f.group_id}）`, f.id));
  }
  selA.value = curA; selB.value = curB;
  fillMarkerSelects();
}
$("selA").addEventListener("change", fillMarkerSelects);
$("selB").addEventListener("change", fillMarkerSelects);

function fillMarkerSelects() {
  const a = fragById(parseInt($("selA").value));
  const b = fragById(parseInt($("selB").value));
  const pa = $("pairA"), pb = $("pairB");
  pa.innerHTML = ""; pb.innerHTML = "";
  if (a) for (const m of a.markers)
    pa.add(new Option(`${m.label} (${round3(m.x_mm)}, ${round3(m.y_mm)})`, m.id));
  if (b) for (const m of b.markers)
    pb.add(new Option(`${m.label} (${round3(m.x_mm)}, ${round3(m.y_mm)})`, m.id));
  state.pickOrderA = [];
  state.pickOrderB = [];
  renderPickHints();
}

// 多选用“点击顺序”决定对应关系
state.pickOrderA = [];
state.pickOrderB = [];
function bindPickOrder(sel, orderKey) {
  sel.addEventListener("click", () => {
    const clicked = [...sel.options].find((o) => o.selected &&
      !state[orderKey].includes(parseInt(o.value)));
    if (clicked) {
      state[orderKey].push(parseInt(clicked.value));
    } else {
      // 取消选择：从顺序中移除
      const kept = [...sel.selectedOptions].map((o) => parseInt(o.value));
      state[orderKey] = state[orderKey].filter((id) => kept.includes(id));
    }
    renderPickHints();
  });
}
function renderPickHints() {
  const ord = (sel, arr) => arr.map((id, i) => {
    const o = [...sel.options].find((x) => parseInt(x.value) === id);
    return `${i + 1}:${o ? o.text.split(" ")[0] : id}`;
  }).join("  ");
  const hintA = $("pairAHint"), hintB = $("pairBHint");
  if (hintA) hintA.textContent = ord($("pairA"), state.pickOrderA) || "（未选）";
  if (hintB) hintB.textContent = ord($("pairB"), state.pickOrderB) || "（未选）";
}
bindPickOrder($("pairA"), "pickOrderA");
bindPickOrder($("pairB"), "pickOrderB");

$("submitCandidate").addEventListener("click", async () => {
  const fa = parseInt($("selA").value);
  const fb = parseInt($("selB").value);
  const aOpts = state.pickOrderA || [];
  const bOpts = state.pickOrderB || [];
  if (!fa || !fb) return toast("请选择两片陶片", "error");
  if (fa === fb) return toast("两片必须是不同陶片", "error");
  if (aOpts.length < 2 || bOpts.length < 2)
    return toast("每片至少按顺序选择两处标记", "error");
  if (aOpts.length !== bOpts.length)
    return toast("两侧标记数量不一致，请按对应顺序选取", "error");
  const pairs = aOpts.map((a, i) => [a, bOpts[i]]);
  try {
    const r = await api("/api/candidates", {
      method: "POST", body: { frag_a: fa, frag_b: fb, pairs },
    });
    toast(`候选关系 #${r.id} 已创建`, "success");
    state.data = r.state;
    renderWorld(); renderSelectors(); renderRelations();
    showPreview(r.preview);
  } catch (e) {
    toast(e.message, "error", 7000);
  }
});

// ---------------------------------------------------------------------------
// 关系列表 + 预览
// ---------------------------------------------------------------------------
function markerLabel(id) {
  for (const f of state.data.fragments) {
    const m = f.markers.find((x) => x.id === id);
    if (m) return `${f.name}.${m.label}`;
  }
  return `#${id}`;
}

function renderRelations() {
  const box = $("relationList");
  if (!state.data.relations.length) {
    box.innerHTML = '<p class="hint">暂无拼接关系。</p>';
    return;
  }
  box.innerHTML = "";
  for (const rel of state.data.relations) {
    const a = fragById(rel.frag_a), b = fragById(rel.frag_b);
    const card = document.createElement("div");
    card.className = "relcard";
    const pairsTxt = rel.pairs
      .map(([x, y]) => `${markerLabel(x)} ↔ ${markerLabel(y)}`).join("；");
    card.innerHTML = `
      <div class="top">
        <b>关系 #${rel.id}</b>
        <span class="tag ${rel.status}">${rel.status === "adopted" ? "已采纳" : "候选"}</span>
      </div>
      <div>${a ? a.name : rel.frag_a} #${rel.frag_a}
           ⇄ ${b ? b.name : rel.frag_b} #${rel.frag_b}
           <span class="sub">v${rel.version}</span></div>
      <div class="pairs">${pairsTxt}</div>
      <button data-act="preview">预览（平移+旋转）</button>
      ${rel.status === "proposed"
        ? `<button data-act="adopt" class="primary">采纳</button>`
        : `<button data-act="undo" class="danger">撤销关系</button>`}
      ${rel.status === "proposed"
        ? `<button data-act="undo" class="subtle">删除候选</button>` : ""}`;
    card.querySelector('[data-act=preview]').addEventListener("click", () =>
      loadPreview(rel.id));
    card.querySelector('[data-act=adopt]').addEventListener("click", () =>
      loadPreview(rel.id, true));
    card.querySelector('[data-act=undo]').addEventListener("click", () =>
      undoRelation(rel));
    box.appendChild(card);
  }
}

async function loadPreview(relId, adoptAfter = false) {
  try {
    const pv = await api(`/api/relations/${relId}/preview`);
    showPreview(pv);
    if (adoptAfter) $("adoptBtn").click();
  } catch (e) {
    toast(e.message, "error", 7000);
  }
}

function showPreview(pv) {
  state.preview = pv;
  state.previewRelId = pv.relation_id;
  $("previewPanel").hidden = false;
  const rel = relById(pv.relation_id) || { frag_a: 0, frag_b: 0 };
  const a = fragById(rel.frag_a), b = fragById(rel.frag_b);
  $("previewTitle").textContent =
    `关系 #${pv.relation_id} · ${pv.mode === "CLOSURE" ? "闭环校验（不移动）"
     : "组合并（B 组整体刚体移动）"} · v${pv.version}`;

  const t = pv.transform;
  const tBox = t
    ? `<div class="transform-box">整体变换（仅平移+旋转）：
         平移 Δ = (${t.dx_mm} mm, ${t.dy_mm} mm)，
         旋转 θ = ${t.rotation_deg}°</div>`
    : `<div class="transform-box">闭环关系：不允许移动，直接校核现有位姿</div>`;

  const rows = pv.pairs.map((p) => `
    <tr class="${p.over_limit ? "bad" : ""}">
      <td>${p.marker_a_label}</td><td>${p.marker_b_label}</td>
      <td>${round3(p.error_mm)} mm</td>
      <td>${p.over_limit ? "❌ 超限" : "✅ ≤" + pv.tolerance_mm + "mm"}</td>
    </tr>`).join("");

  const moveRows = pv.moving_fragments.map((m) => `
    <tr>
      <td>${m.name} #${m.id}</td>
      <td>(${round3(m.current.x_mm)}, ${round3(m.current.y_mm)}) mm，
          θ=${round3(m.current.angle_deg)}°</td>
      <td>(${round3(m.proposed.x_mm)}, ${round3(m.proposed.y_mm)}) mm，
          θ=${round3(m.proposed.angle_deg)}°</td>
    </tr>`).join("");

  const conflicts = pv.conflicts.length
    ? pv.conflicts.map((c) => `<div class="conflict">⛔ ${c.message}</div>`).join("")
    : '<div class="pvsummary ok">✅ 无冲突：对应点误差均不超过 '
      + `${pv.tolerance_mm} mm，同组轮廓内部不重叠。</div>`;

  $("previewBody").innerHTML = `
    ${tBox}
    <div class="pvsummary ${pv.valid ? "ok" : "bad"}">
      ${pv.valid ? "✅ 预览通过，可以采纳。" : "❌ 存在冲突，采纳将被整次拒绝，原拼接组不变。"}
    </div>
    <table class="pv">
      <tr><th>A 标记</th><th>B 标记</th><th>误差</th><th>判定</th></tr>
      ${rows}
    </table>
    ${moveRows ? `<h3 class="sub">各片位置与角度</h3>
      <table class="pv">
        <tr><th>陶片</th><th>当前位姿</th><th>预览位姿</th></tr>${moveRows}
      </table>` : ""}
    <h3 class="sub">冲突原因</h3>${conflicts}`;

  const adoptBtn = $("adoptBtn");
  adoptBtn.disabled = !pv.valid;
  adoptBtn.textContent = pv.valid ? "采纳拼接" : "存在冲突，无法采纳";
  renderWorld();
}

$("closePreview").addEventListener("click", () => {
  $("previewPanel").hidden = true;
  state.preview = null;
  renderWorld();
});

$("adoptBtn").addEventListener("click", async () => {
  const pv = state.preview;
  if (!pv) return;
  try {
    const r = await api(`/api/relations/${pv.relation_id}/adopt`, {
      method: "POST",
      body: { version: pv.version, state_version: pv.state_version },
    });
    toast(`关系 #${r.relation_id} 已采纳（v${r.version}）`, "success");
    state.data = r.state;
    state.preview = null;
    $("previewPanel").hidden = true;
    renderWorld(); renderSelectors(); renderRelations();
  } catch (e) {
    if (e.code === "VERSION_CONFLICT" || e.code === "PREVIEW_STALE") {
      toast(`⚠️ ${e.message}`, "error", 8000);
      const fresh = await api(`/api/relations/${pv.relation_id}/preview`).catch(() => null);
      if (fresh) { state.data = await api("/api/state"); renderSelectors(); renderRelations(); showPreview(fresh); }
    } else if (e.code === "ADOPT_REJECTED" && e.details && e.details.preview) {
      toast("整次拒绝：原拼接组不变", "error", 6000);
      showPreview(e.details.preview);
    } else {
      toast(e.message, "error", 7000);
    }
  }
});

async function undoRelation(rel) {
  const word = rel.status === "adopted"
    ? `撤销已采纳关系 #${rel.id}？将按剩余关系重算连通组，孤片恢复独立组（保留其世界位姿）。`
    : `删除候选关系 #${rel.id}？`;
  if (!confirm(word)) return;
  try {
    const r = await api(`/api/relations/${rel.id}/undo`, {
      method: "POST", body: { version: rel.version },
    });
    toast(r.was_adopted
      ? `关系已撤销，连通组已重算（状态 v${r.state_version}）`
      : "候选已删除", "success");
    state.data = r.state;
    if (state.previewRelId === rel.id) {
      state.preview = null;
      $("previewPanel").hidden = true;
    }
    renderWorld(); renderSelectors(); renderRelations();
  } catch (e) {
    if (e.code === "VERSION_CONFLICT") {
      toast(`⚠️ 版本冲突：${e.message}`, "error", 8000);
      await refresh();
    } else toast(e.message, "error", 7000);
  }
}

$("refreshBtn").addEventListener("click", () =>
  refresh().then(() => toast("状态已刷新", "info", 1500))
    .catch((e) => toast(e.message, "error")));

// ---------------------------------------------------------------------------
// 初始
// ---------------------------------------------------------------------------
(async function init() {
  drawEditor();
  try {
    await refresh();
    // 自动适应世界视图
    fitWorldView();
  } catch (e) {
    toast("初始化失败：" + e.message, "error", 10000);
  }
})();

function fitWorldView() {
  if (!state.data.fragments.length) return;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const f of state.data.fragments)
    for (const [x, y] of f.world_contour_mm) {
      minX = Math.min(minX, x); minY = Math.min(minY, y);
      maxX = Math.max(maxX, x); maxY = Math.max(maxY, y);
    }
  const w = state.world;
  const pad = 20;
  w.scale = Math.min(
    (wCanvas.width - 2 * pad) / Math.max(maxX - minX, 1),
    (wCanvas.height - 2 * pad) / Math.max(maxY - minY, 1));
  w.scale = Math.min(12, Math.max(1, w.scale));
  w.ox = pad - minX * w.scale + (wCanvas.width - 2 * pad - (maxX - minX) * w.scale) / 2;
  w.oy = wCanvas.height - pad + minY * w.scale
       - (wCanvas.height - 2 * pad - (maxY - minY) * w.scale) / 2;
  renderWorld();
}
