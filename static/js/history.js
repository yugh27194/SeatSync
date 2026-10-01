/* /history — 내 이용 기록: 일/주/월별 이용 시간, 요약, 예약별 연장·반납 이력, 경고·알림 기록 */
(function () {
  "use strict";
  const { api, fmtTime, esc } = window.SS;
  const $ = (id) => document.getElementById(id);
  const TITLES = { day: "일별 이용 시간 (최근 14일)", week: "주별 이용 시간 (최근 8주)", month: "월별 이용 시간 (최근 6개월)" };
  const UNIT = { day: "이용한 날", week: "이용한 주", month: "이용한 달" };
  const STATUS_CLS = { returned: "green", expired: "", in_use: "blue", reserved: "blue", no_show: "red", force_returned: "red", cancelled: "" };
  let period = "day";

  function hm(min) {
    min = Math.round(min || 0);
    const h = Math.floor(min / 60), m = min % 60;
    return h ? `${h}시간${m ? " " + m + "분" : ""}` : `${m}분`;
  }
  function md(iso) { return iso ? `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}` : ""; }

  function renderSummary(sm) {
    const cards = [
      ["총 이용 시간", hm(sm.total_min), ""],
      [`평균 (${UNIT[period]})`, hm(sm.avg_min), `${sm.active_buckets}${period === "day" ? "일" : period === "week" ? "주" : "달"} 이용`],
      ["예약", `${sm.reservations}회`, `최장 ${hm(sm.longest_min)}`],
      ["연장", `${sm.extends}회`, ""],
      ["반납", `${sm.returns}회`, `시간 만료 ${sm.expired}회`],
      ["미입실 · 강제 반납", `${sm.no_shows} · ${sm.force_returns}회`, sm.no_shows + sm.force_returns ? "규정 위반 기록" : "없음"],
    ];
    $("summary").innerHTML = cards.map(([k, v, sub]) =>
      `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div>${sub ? `<div class="s">${sub}</div>` : ""}</div>`).join("");
  }

  function renderBars(buckets) {
    const max = Math.max(60, ...buckets.map((b) => b.minutes));
    $("bars").innerHTML = buckets.map((b) => `<div class="vb" title="${esc(b.label)} · ${hm(b.minutes)} · ${b.sessions}회">
        <span class="vb-val">${b.minutes ? hm(b.minutes).replace("시간", "h").replace("분", "m").replace(" ", "") : ""}</span>
        <div class="vb-bar${b.minutes ? "" : " zero"}" style="height:${Math.max(2, (b.minutes / max) * 100)}%"></div>
        <span class="vb-x">${esc(b.label)}</span></div>`).join("");
  }

  async function loadHistory() {
    const d = await api("GET", "/api/me/history?period=" + period);
    $("chart-title").textContent = TITLES[period];
    renderSummary(d.summary);
    renderBars(d.buckets);
  }

  async function loadReservations() {
    const d = await api("GET", "/api/me/reservations");
    $("res-list").innerHTML = d.reservations.length ? d.reservations.map((r) => `<li class="res-item">
      <div class="ri-head"><b>${md(r.start_at)} · ${esc(r.seat_label)}</b> <span class="muted small">${esc(r.zone || "")}</span>
        <span class="pill ${STATUS_CLS[r.status] || ""}">${esc(r.status_label)}</span>
        <span class="ri-used">${r.used_min ? hm(r.used_min) + " 이용" : "이용 안 함"}</span></div>
      <div class="muted small">${fmtTime(r.start_at)} 예약 · 종료 ${r.ended_at ? fmtTime(r.ended_at) : fmtTime(r.end_at) + " 예정"}${r.extend_count ? ` · 연장 ${r.extend_count}회` : ""}</div>
      <ol class="timeline">${r.events.map((e) => `<li class="ev-${esc(e.kind)}"><span class="t">${fmtTime(e.at)}</span>${esc(e.label)}${e.memo ? ` <span class="muted">(${esc(e.memo)})</span>` : ""}</li>`).join("")}</ol>
    </li>`).join("") : `<li class="muted small">아직 이용 기록이 없어요.</li>`;
  }

  async function loadNotices() {
    const d = await api("GET", "/api/me/notifications");
    const list = d.items.filter((n) => ["prewarn", "issue", "warning", "suspend"].includes(n.kind));
    $("notice-list").innerHTML = list.length ? list.map((n) => `<li class="res-item nl-item lv-${esc(n.level)}">
      <div class="ri-head"><b>${esc(n.title)}</b><span class="ri-used muted small">${md(n.created_at)} ${fmtTime(n.created_at)}</span></div>
      ${n.body ? `<div class="small">${esc(n.body)}</div>` : ""}</li>`).join("")
      : `<li class="muted small">받은 경고가 없어요.</li>`;
  }

  $("period").addEventListener("click", (e) => {
    const b = e.target.closest("[data-period]");
    if (!b) return;
    period = b.dataset.period;
    $("period").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    loadHistory();
  });
  loadHistory();
  loadReservations();
  loadNotices();
})();
