/* /map — 실시간 좌석 지도 (3초 polling). 초록 빈자리 · 주황 사용중 · 회색 사용불가.
   관리자 확인이 필요한 요소는 [관리자] 탭에만 있다. */
(function () {
  "use strict";
  const { api, layoutGrid, poll, fmtRemain, fmtTime, syncClock, serverNow, parseTs, toast, modal, esc } = window.SS;
  const mapEl = document.getElementById("seatmap");
  const barEl = document.getElementById("mybar");
  const joinBtn = document.getElementById("wl-join");
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
    const html = [layoutGrid(mapEl, data, 56)];
    for (const s of data.seats) {
      const sub = SUB[s.view];
      html.push(
        `<button type="button" class="seat v-${s.view}${s.booth ? " booth" : ""}" data-no="${s.no}"
          style="grid-column:${s.x};grid-row:${s.y}" title="${esc(s.zone || "")}"
          aria-label="${esc(s.label)} ${esc(sub)}">
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
    barEl.hidden = false;
    joinBtn.hidden = true;
    if (!r && me.suspended_until) {
      barEl.innerHTML = `<div class="txt"><span class="deadline">이용 정지 중</span> · ${me.suspended_until.slice(5, 10)} ${fmtTime(me.suspended_until)}까지 예약할 수 없어요</div>`;
      return;
    }
    if (r) {
      const txt = r.status === "reserved"
        ? (parseTs(r.checkin_deadline) > now
          ? `<strong>${esc(r.seat_label)}</strong> · 예약됨<br><span class="deadline">${fmtRemain(parseTs(r.checkin_deadline) - now)}</span> 안에 좌석 QR로 체크인해 주세요`
          : `<strong>${esc(r.seat_label)}</strong> · 예약됨<br><span class="deadline">체크인 시간이 지났어요.</span> 좌석 QR로 바로 체크인해 주세요`)
        : `<strong>${esc(r.seat_label)}</strong> · 남은 시간 <strong>${fmtRemain(parseTs(r.end_at) - now)}</strong>`;
      barEl.innerHTML = `<div class="txt">${txt}</div><a class="btn small" href="/my">내 자리 관리</a>${statusLine()}`;
      return;
    }
    if (w && w.status === "offered") {
      const left = parseTs(w.offer.expires_at) - now;
      barEl.innerHTML = `<div class="txt"><strong>${esc(w.offer.seat_label)}</strong> 자리가 났어요!<br>
        <span class="deadline">${SS.fmtClock(left)}</span> 동안 먼저 예약할 수 있어요</div>
        <div class="btn-row"><button class="btn small" data-wl="take">바로 예약</button>
        <button class="btn small secondary" data-wl="decline">양보</button></div>`;
      return;
    }
    if (w) {
      barEl.innerHTML = `<div class="txt">🔔 빈자리 알림 대기 중 · <strong>${w.position}번째</strong>${w.zone ? ` · ${esc(w.zone)}` : " · 아무 자리"}<br>
        <span class="muted small">자리가 나면 알려 드릴게요.</span></div>
        <button class="btn small secondary" data-wl="cancel">대기 취소</button>`;
      return;
    }
    // 예약·대기가 없으면 상단 바는 숨기고, 빈자리 알림 버튼은 지도 아래 오른쪽에 둔다.
    barEl.hidden = true;
    joinBtn.hidden = false;
  }

  function renderLive() {
    const l = data.live;
    const el = document.getElementById("live");
    if (!l || !el) return;
    const pct = Math.round(l.occupancy * 100);
    const lvl = pct >= 85 ? "high" : pct >= 60 ? "mid" : "low";
    el.innerHTML = `<span class="live-dot ${lvl}"></span>지금 <b>${pct}%</b> 사용중 · 빈자리 <b>${l.available}</b>석 / ${l.usable}석
      <a href="/congestion">혼잡도 보기 →</a>`;
  }

  async function load() {
    data = await api("GET", "/api/seats", null, { quiet: true });
    syncClock(data.server_time);
    renderMap();
    renderBar();
    renderLive();
  }

  async function waitlistAction(kind) {
    const w = data.waitlist;
    if (kind === "join") {
      const zones = Object.keys(data.zones || {});
      const v = await modal({ title: "🔔 빈자리 알림 받기",
        body: `자리가 나면 순서대로 알려 드려요.\n알림을 받으면 ${SS.fmtMin(data.policy.hold_min)} 동안 먼저 예약할 수 있어요.`,
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
  joinBtn.addEventListener("click", () => waitlistAction("join"));
  barEl.addEventListener("click", (e) => {
    const b = e.target.closest("[data-wl]");
    if (b) waitlistAction(b.dataset.wl);
  });

  async function reserve(seat) {
    const p = data.policy;
    const ok = await modal({ title: `${seat.label} 좌석을 예약할까요?`,
      body: `이용 시간 ${fmtMinutes(p.default_use_min)}\n예약 후 ${SS.fmtMin(p.checkin_limit_min)} 안에 좌석 QR로 체크인해 주세요.`,
      ok: "예약하기" });
    if (!ok) return;
    busy = true;
    try {
      await api("POST", "/api/reservations", { seat_no: seat.no });
      toast(`${seat.label} 예약 완료! 좌석 QR로 체크인해 주세요.`, "ok");
      SS.refreshNotices();
    } catch (e) { /* 토스트 표시됨 */ }
    finally { busy = false; await ticker.refresh(); }
  }

  mapEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".seat");
    if (!btn || !data || busy) return;
    const seat = data.seats.find((s) => s.no === Number(btn.dataset.no));
    if (!seat) return;
    if (seat.view === "mine") { location.href = "/my"; return; }
    if (seat.view === "held") { toast("다른 대기자에게 먼저 안내 중인 좌석이에요."); return; }
    if (seat.view === "available" || seat.view === "offered") {
      if (data.my_reservation) { toast("이미 예약한 좌석이 있어요.", "error"); return; }
      reserve(seat);
      return;
    }
    // 사용중·사용불가 좌석: 이유는 알리지 않는다
    const msg = seat.view === "unavailable"
      ? "사용할 수 없는 좌석입니다. 관리자에게 문의해 주세요."
      : "다른 사용자가 사용 중인 좌석입니다.";
    modal({ title: seat.label, body: msg, ok: "닫기", noCancel: true });
  });

  const ticker = poll(load, 3000);
  setInterval(() => { if (data) renderBar(); }, 1000);
})();
