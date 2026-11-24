// Draws the report from the model report.py embeds. Board and run text is set with textContent only.
(function () {
  "use strict";
  const model = JSON.parse(document.getElementById("model").textContent);
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const SVG = "http://www.w3.org/2000/svg";
  const svgEl = (tag, attrs) => {
    const n = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  };
  const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : null);

  function ago(iso) {
    if (!iso) return "never";
    const mins = Math.round((Date.now() - Date.parse(iso)) / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    if (mins < 48 * 60) return `${Math.round(mins / 60)}h ago`;
    return `${Math.round(mins / 1440)}d ago`;
  }

  function header() {
    $("name").textContent = `${model.name} factory`;
    const at = new Date(model.generatedAt);
    $("updated").textContent = `Updated ${ago(model.generatedAt)} · ${at.toLocaleString()}`;
    $("updated").title = model.generatedAt;
    const open = model.board.columns.reduce((n, c) => n + c.count, 0);
    const unhealthy = model.health.filter((w) => w.state === "failing" || w.state === "stalled").length;
    const pills = [
      [model.actOn.length, "to act on", model.actOn.length > 0],
      [open, "open items", false],
      [unhealthy, unhealthy === 1 ? "worker unhealthy" : "workers unhealthy", unhealthy > 0],
    ];
    for (const [n, label, bad] of pills) {
      const p = el("span", bad ? "pill bad" : "pill");
      p.append(el("b", "", String(n)), document.createTextNode(` ${label}`));
      $("summary").append(p);
    }
  }

  function actOn() {
    const list = $("act");
    if (!model.actOn.length) {
      list.append(el("li", "empty", "Nothing waits on a person."));
      return;
    }
    const tpl = $("act-row");
    for (const row of model.actOn) {
      const li = tpl.content.firstElementChild.cloneNode(true);
      const item = li.querySelector(".item");
      item.textContent = row.id;
      if (safeUrl(row.url)) item.href = row.url;
      li.querySelector(".title").textContent = row.title;
      li.querySelector(".why").textContent = row.why;
      const pr = li.querySelector(".pr");
      if (safeUrl(row.link)) {
        pr.href = row.link;
        pr.textContent = `#${row.link.replace(/[#?].*$/, "").replace(/\/+$/, "").split("/").pop()}`;
      }
      list.append(li);
    }
  }

  function board() {
    const b = model.board;
    for (const c of b.columns) {
      const box = el("div", "column");
      box.append(el("b", "", String(c.count)), el("span", "", c.name));
      $("columns").append(box);
    }
    const svg = $("chart");
    const [w, h, l, r, t, bottom] = [900, 200, 36, 12, 16, 26];
    const vals = b.open;
    const top = Math.max(1, ...vals);
    const x = (k) => l + (vals.length > 1 ? (k * (w - l - r)) / (vals.length - 1) : (w - l - r) / 2);
    const y = (v) => h - bottom - (v / top) * (h - t - bottom);
    for (const v of [0, Math.round(top / 2), top]) {
      svg.append(svgEl("line", { class: "grid", x1: l, x2: w - r, y1: y(v), y2: y(v) }));
      const label = svgEl("text", { x: l - 6, y: y(v), "text-anchor": "end", "dominant-baseline": "middle" });
      label.textContent = String(v);
      svg.append(label);
    }
    const pts = vals.map((v, k) => `${x(k).toFixed(1)},${y(v).toFixed(1)}`);
    if (pts.length) {
      svg.append(svgEl("polygon", { class: "area", points: `${x(0)},${y(0)} ${pts.join(" ")} ${x(vals.length - 1)},${y(0)}` }));
      svg.append(svgEl("polyline", { class: "line", points: pts.join(" ") }));
      const last = svgEl("circle", { class: "dot", cx: x(vals.length - 1), cy: y(vals[vals.length - 1]), r: 3.5 });
      svg.append(last);
      for (const [k, anchor] of [[0, "start"], [vals.length - 1, "end"]]) {
        const d = svgEl("text", { x: x(k), y: h - 6, "text-anchor": anchor });
        d.textContent = b.days[k];
        svg.append(d);
      }
    }
    $("chart-cap").textContent =
      `Open items at the end of each day, last ${b.days.length} days · ${b.opened} opened, ${b.closed} closed`;
  }

  function health() {
    const body = $("health");
    for (const w of model.health) {
      const tr = el("tr");
      tr.append(el("td", "", w.worker));
      const state = el("td");
      state.append(el("span", `state ${w.state.replace(/\s+/g, "-")}`, w.state));
      tr.append(state);
      for (const iso of [w.last, w.lastOk]) {
        const td = el("td", "when", ago(iso));
        if (iso) td.title = iso;
        tr.append(td);
      }
      tr.append(el("td", "err", w.error));
      body.append(tr);
    }
  }

  header();
  actOn();
  board();
  health();
  for (const n of model.notes) $("notes").append(el("p", "note", n));
})();
