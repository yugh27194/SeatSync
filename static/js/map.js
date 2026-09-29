/* /map — 실시간 좌석 지도 (3초 polling). 초록 빈자리 · 주황 사용중 · 회색 사용불가.
   확인 필요(붉은 강조 + !)는 관리자 모드에서만 보인다. */
(function () {
  "use strict";
  const { api, layoutGrid, poll, fmtRemain, fmtTime, syncClock, serverNow, parseTs, toast, modal, esc } = window.SS;
  const mapEl = document.getElementById("seatmap");
  const barEl = document.getElementById("mybar");
  const attnBar = document.getElementById("attn-bar");
  let data = null;
  let busy = false;

  const SUB = { available: "빈자리", taken: "사용중", unavailable: "사용불가", mine: "내 자리",
    offered: "내게 안내됨", held: "대기자 안내 중" };

  function fmtMinutes(min) {
    if (min < 60) return SS.fmtMin(min);
    const h = Math.floor(min / 60), m = Math.round(min % 60);
    return h + "시간" + (m ? " " + m + "분" : "");
  }

  function renderMap() {
    const admin = data.me.admin;
    const html = [layoutGrid(mapEl, data, 56)];
    for (const s of data.seats) {
      const attn = admin && s.attention;
      const sub = SUB[s.view];
      html.push(
        `<button type="button" class="seat v-${s.view}${s.booth ? " booth" : ""}${attn ? " check" : ""}" data-no="${s.no}"
          style="grid-column:${s.x};grid-row:${s.y}" title="${esc(s.zone || "")}${attn ? ` · 확인 필요: ${esc(s.detail_label)}` : ""}"
          aria-label="${esc(s.label)} ${esc(sub)}${attn ? " · 확인 필요" : ""}">
          ${attn ? '<span class="bang" aria-hidden="true">!</span>' : ""}
          ${esc(s.label)}</button>`
      );
    }
    mapEl.innerHTML = html.join("");
  }

  function statusLine() {
    const st = data.my_status;
    return st ? `<div class="own-status lv-${st.level}">⚠ ${esc(st.message)}</div>` : "";
  }

  function renderBar() {
    const r = data.my_reservation;
    const me = data.me || {};
    const w = data.waitlist;
    const now = serverNow();
    if (!r && me.suspended_until) {
      barEl.innerHTML = `<div class="txt"><span class="deadline">이용 정지 중</span> · ${me.suspended_until.slice(5, 10)} ${fmtTime(me.suspended_until)}까지 예약할 수 없어요.</div>`;
      return;
    }
    if (r) {
      const txt = r.status === "reserved"
        ? (parseTs(r.checkin_deadline) > now
          ? `<strong>${esc(r.seat_label)}</strong> · 예약됨<br><span class="deadline">${fmtRemain(parseTs(r.checkin_deadline) - now)}</span> 안에 좌석 QR로 체크인하세요`
          : `<strong>${esc(r.seat_label)}</strong> · 예약됨<br><span class="deadline">체크인 시간이 지났어요.</span> 도착했다면 바로 좌석 QR로 체크인하세요`)
        : `<strong>${esc(r.seat_label)}</strong> · 남은 시간 <strong>${fmtRemain(parseTs(r.end_at) - now)}</strong>`;
      barEl.innerHTML = `<div class="txt">${txt}</div><a class="btn small" href="/my">내 자리 관리</a>${statusLine()}`;
      return;
    }
    if (w && w.status === "offered") {
      const left = parseTs(w.offer.expires_at) - now;
      barEl.innerHTML = `<div class="txt"><strong>${esc(w.offer.seat_label)}</strong> 빈자리가 안내됐어요!<br>
        <span class="deadline">${SS.fmtClock(left)}</span> 안에 예약하면 먼저 이용할 수 있어요.</div>
        <div class="btn-row"><button class="btn small" data-wl="take">바로 예약</button>
        <button class="btn small secondary" data-wl="decline">양보</button></div>`;
      return;
    }
    if (w) {
      barEl.innerHTML = `<div class="txt">🔔 빈자리 알림 대기 중 · <strong>${w.position}번째</strong>${w.zone ? ` · ${esc(w.zone)}` : " · 아무 자리"}<br>
        <span class="muted small">빈자리가 나면 ${SS.fmtMin(data.policy.hold_min)} 동안 먼저 예약할 수 있게 알려 드려요.</span></div>
        <button class="btn small secondary" data-wl="cancel">대기 취소</button>`;
      return;
    }
    const free = data.seats.filter((x) => x.view === "available").length;
    barEl.innerHTML = `<div class="txt">${free ? "빈 좌석을 눌러 예약하세요" : "지금은 빈자리가 없어요"}</div>
      <button class="btn small ${free ? "secondary" : ""}" data-wl="join">🔔 빈자리 알림</button>`;
  }

  function renderLive() {
    const l = data.live;
    const el = document.getElementById("live");
    if (!l || !el) return;
    const pct = Math.round(l.occupancy * 100);
    const lvl = pct >= 85 ? "high" : pct >= 60 ? "mid" : "low";
    el.innerHTML = `<span class="live-dot ${lvl}"></span>지금 <b>${pct}%</b> 사용중 · 빈자리 <b>${l.available}</b>석 / ${l.usable}석
      ${l.actual_rate != null ? ` · 실사용 ${Math.round(l.actual_rate * 100)}%` : ""} <a href="/congestion">혼잡도 보기 →</a>`;
  }

  function renderAttention() {
    if (!attnBar) return;
    const list = data.me.admin ? data.seats.filter((s) => s.attention) : [];
    attnBar.classList.toggle("hidden", !list.length);
    if (list.length) {
      document.getElementById("attn-text").textContent = `확인 필요 ${list.length}석 · ${list.map((s) => s.label).join(", ")}`;
    }
  }

  async function load() {
    data = await api("GET", "/api/seats", null, { quiet: true });
    syncClock(data.server_time);
    renderMap();
    renderBar();
    renderAttention();
    renderLive();
  }

  async function waitlistAction(kind) {
    const w = data.waitlist;
    if (kind === "join") {
      const zones = Object.keys(data.zones || {});
      const v = await modal({ title: "🔔 빈자리 알림 받기",
        body: `빈자리가 나면 대기 순서대로 알려 드리고, ${SS.fmtMin(data.policy.hold_min)} 동안 먼저 예약할 수 있게 자리를 잡아 둡니다.\n(이 화면을 열어 두면 알림을 바로 받을 수 있어요)`,
        fields: [{ name: "zone", label: "원하는 구역", type: "select", value: "",
          options: [{ value: "", label: "아무 자리" }].concat(zones.map((z) => ({ value: z, label: `${z} · ${data.zones[z]}` }))) }],
        ok: "알림 신청" });
      if (!v) return;
      try { await api("POST", "/api/waitlist", { zone: v.zone }); toast("빈자리 알림을 신청했어요.", "ok"); } catch (e) { /* 토스트 */ }
    } else if (kind === "cancel") {
      if (!(await modal({ title: "대기 취소", body: "빈자리 알림 대기를 취소할까요?", ok: "취소하기" }))) return;
      try { await api("POST", "/api/waitlist/cancel"); toast("대기를 취소했어요."); } catch (e) { /* 토스트 */ }
    } else if (kind === "decline") {
      try { await api("POST", "/api/waitlist/decline"); toast("다음 대기자에게 양보했어요."); } catch (e) { /* 토스트 */ }
    } else if (kind === "take") {
      const seat = data.seats.find((x) => x.no === w.offer.seat_no);
      if (seat) return reserve(seat);
    }
    await ticker.refresh();
  }
  barEl.addEventListener("click", (e) => {
    const b = e.target.closest("[data-wl]");
    if (b) waitlistAction(b.dataset.wl);
  });

  async function reserve(seat) {
    const p = data.policy;
    const ok = await modal({ title: `${seat.label} 좌석을 예약할까요?`,
      body: `${seat.zone ? seat.zone + " · " : ""}이용 시간 ${fmtMinutes(p.default_use_min)}, ${SS.fmtMin(p.checkin_limit_min)} 안에 체크인 필요`,
      ok: "예약하기" });
    if (!ok) return;
    busy = true;
    try {
      await api("POST", "/api/reservations", { seat_no: seat.no });
      toast(`${seat.label} 좌석을 예약했어요. 좌석 QR로 체크인해 주세요.`, "ok");
      SS.refreshNotices();
    } catch (e) { /* 토스트 표시됨 */ }
    finally { busy = false; await ticker.refresh(); }
  }

  mapEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".seat");
    if (!btn || !data || busy) return;
    const seat = data.seats.find((s) => s.no === Number(btn.dataset.no));
    if (!seat) return;
    if (data.me.admin && seat.view !== "mine") { location.href = "/admin?seat=" + seat.no; return; }
    if (seat.view === "mine") { location.href = "/my"; return; }
    if (seat.view === "held") { toast("빈자리 알림 대기자에게 먼저 안내 중인 좌석이에요. 잠시 후 다시 확인해 주세요."); return; }
    if (seat.view === "available" || seat.view === "offered") {
      if (data.my_reservation) { toast("이미 예약한 좌석이 있어요. 반납 후 다시 예약해 주세요.", "error"); return; }
      reserve(seat);
      return;
    }
    // 사용중·사용불가 좌석: 관리자 화면과 같은 상태 이름으로 안내 (색은 주황/회색 그대로)
    modal({ title: `${seat.label} · ${seat.state_label}`, body: seat.state_msg || "", ok: "닫기", noCancel: true });
  });

  const ticker = poll(load, 3000);
  setInterval(() => { if (data) renderBar(); }, 1000);
})();
