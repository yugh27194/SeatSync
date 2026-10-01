/* /congestion — 혼잡도: 지금 상태, 오늘 시간대별, 요일×시간 히트맵.
   관리자 탭의 /admin/congestion(이용 분석)에서는 실사용률·유휴 점유·처리 필요 비율 추가 */
(function () {
  "use strict";
  const { api, poll, esc } = window.SS;
  const $ = (id) => document.getElementById(id);
  const METRICS = {
    occupancy: ["점유율", "좌석이 예약·사용중인 비율"],
    actual: ["실사용률", "사람이 실제로 앉아 있는 비율"],
    idle: ["유휴 점유", "자리는 차지했지만 비어 있는 비율(입실 대기·일시 이석·짐만 있음·장기 이석·사석화)"],
    issue: ["처리 필요", "장기 이석·사석화·무단 점유·판단 불가 등 처리 필요 상태 비율"],
  };
  let data = null, metric = "occupancy";

  const pct = (v) => (v == null ? "-" : Math.round(v * 100) + "%");
  function color(v, m) {
    if (v == null) return "var(--hm-none)";
    const x = Math.min(1, v / (m === "issue" ? 0.3 : m === "idle" ? 0.5 : 0.9));
    const hue = 140 - 140 * x;  // 초록 → 빨강
    return `hsl(${hue} 70% ${88 - 40 * x}%)`;
  }

  function renderLive() {
    const l = data.live;
    const p = Math.round(l.occupancy * 100);
    const lvl = p >= 85 ? ["high", "혼잡"] : p >= 60 ? ["mid", "보통"] : ["low", "여유"];
    $("live-card").innerHTML = `<div class="gauge ${lvl[0]}" style="--p:${p}"><div class="g-val">${p}%</div><div class="g-lbl">${lvl[1]}</div></div>
      <div class="live-info"><b>지금 ${l.in_use}석 사용중</b> · 빈자리 ${l.available}석 (사용 가능 ${l.usable}석)
      ${l.actual_rate != null ? `<br>실제 착석 ${l.actual}석 · 실사용률 ${Math.round(l.actual_rate * 100)}% <span class="muted small">(관리자)</span>` : ""}
      ${data.quiet ? `<br><span class="muted small">보통 가장 한산한 때: ${data.quiet.weekday}요일 ${data.quiet.hour}시</span>` : ""}</div>`;
  }

  function renderToday() {
    const rows = data.today;
    const admin = data.admin;
    $("today").innerHTML = rows.map((r) => {
      const o = r.occupancy, a = r.actual;
      return `<div class="vb" title="${r.hour}시 · 점유 ${pct(o)}${admin ? " · 실사용 " + pct(a) : ""}">
        <span class="vb-val">${o != null && o > 0 ? pct(o) : ""}</span>
        <div class="vb-bar${o ? "" : " zero"}${o == null ? " future" : ""}" style="height:${o == null ? 2 : Math.max(2, o * 100)}%;background:${o == null ? "" : color(o, "occupancy")}">
          ${admin && a ? `<div class="vb-inner" style="height:${o ? (a / o) * 100 : 0}%"></div>` : ""}</div>
        <span class="vb-x">${r.hour}</span></div>`;
    }).join("");
    $("today-legend").innerHTML = admin
      ? `<span><i style="background:hsl(80 70% 70%)"></i>점유율</span><span><i style="background:rgba(20,40,80,.35)"></i>그중 실제 착석</span>`
      : `<span class="muted small">막대 높이 = 그 시간대에 사용중이던 좌석 비율</span>`;
  }

  function renderHeatmap() {
    const g = data[metric];
    $("hm-title").textContent = `요일·시간대별 ${METRICS[metric][0]} (최근 ${data.weeks}주 평균)`;
    const head = `<tr><th></th>${data.hours.map((h) => `<th>${h}</th>`).join("")}</tr>`;
    const body = data.days.map((d, wd) => `<tr><th>${d}</th>${g[wd].map((v, i) =>
      `<td style="background:${color(v, metric)}" title="${d}요일 ${data.hours[i]}시 · ${METRICS[metric][0]} ${pct(v)}"><span>${v == null ? "" : Math.round(v * 100)}</span></td>`).join("")}</tr>`).join("");
    $("heatmap").innerHTML = head + body;
    const ins = [];
    if (data.peak) ins.push(`가장 붐비는 때: <b>${data.peak.weekday}요일 ${data.peak.hour}시</b> (평균 ${pct(data.peak.rate)})`);
    if (data.quiet) ins.push(`가장 한산한 때: <b>${data.quiet.weekday}요일 ${data.quiet.hour}시</b> (평균 ${pct(data.quiet.rate)})`);
    if (data.admin) {
      const avg = (grid) => { const v = grid.flat().filter((x) => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : 0; };
      const o = avg(data.occupancy), a = avg(data.actual), i = avg(data.idle);
      ins.push(`평균 점유율 ${pct(o)} 중 실제 착석 ${pct(a)} → 예약 대비 실사용 <b>${o ? pct(a / o) : "-"}</b>, 유휴 점유 ${pct(i)} (관리자)`);
    }
    ins.push(`<span class="muted">${METRICS[metric][1]}. 셀 숫자는 %입니다.</span>`);
    $("insights").innerHTML = ins.map((x) => `<li>${x}</li>`).join("");
  }

  function renderMetricTabs() {
    const el = $("metric");
    el.classList.toggle("hidden", !data.admin);
    if (!data.admin) { metric = "occupancy"; return; }
    el.innerHTML = Object.keys(METRICS).map((k) => `<button type="button" data-m="${k}" class="${k === metric ? "on" : ""}">${METRICS[k][0]}</button>`).join("");
  }
  $("metric").addEventListener("click", (e) => {
    const b = e.target.closest("[data-m]");
    if (!b) return;
    metric = b.dataset.m;
    renderMetricTabs();
    renderHeatmap();
  });

  async function load() {
    // 관리자 탭의 [이용 분석](/admin/congestion)에서만 실사용률·유휴 점유·처리 필요 비율을 받는다
    const scope = location.pathname.startsWith("/admin") ? "?scope=admin" : "";
    data = await api("GET", "/api/congestion" + scope, null, { quiet: true });
    $("updated").textContent = "갱신 " + data.server_time.slice(11, 16);
    renderLive(); renderToday(); renderMetricTabs(); renderHeatmap();
  }
  poll(load, 60000);
})();
