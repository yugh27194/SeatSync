/* /map — 실시간 좌석 지도 (3초 polling). 처리 필요(!) 표시는 관리자 모드에서만 보인다. */
(function () {
  "use strict";
  const { api, layoutGrid, poll, fmtRemain, fmtTime, syncClock, serverNow, parseTs, toast, modal, esc } = window.SS;
  const mapEl = document.getElementById("seatmap");
  const barEl = document.getElementById("mybar");
  const attnBar = document.getElementById("attn-bar");
  let data = null;
  let busy = false;

  const SUB = { available: "빈자리", taken: "사용중", unavailable: "사용불가", mine: "내 자리" };

  function fmtMinutes(min) {
    const h = Math.floor(min / 60), m = min % 60;
    return (h ? h + "시간" : "") + (h && m ? " " : "") + (m ? m + "분" : "");
  }

  function renderMap() {
    const admin = data.me.admin;
    const html = [layoutGrid(mapEl, data, 56)];
    for (const s of data.seats) {
      const attn = admin && s.attention;
      // 관리자 모드에서는 세부 상태(짐만 있음, 고장 등)를 함께 보여 준다
      const sub = admin && s.view !== "mine" && s.detail_label ? s.detail_label : SUB[s.view];
      html.push(
        `<button type="button" class="seat v-${s.view}${s.booth ? " booth" : ""}${attn ? " attn" : ""}" data-no="${s.no}"
          style="grid-column:${s.x};grid-row:${s.y}" title="${esc(s.zone || "")}"
          aria-label="${esc(s.label)} ${esc(sub)}${attn ? " · 처리 필요" : ""}">
          ${attn ? '<span class="bang" aria-hidden="true">!</span>' : ""}
          ${esc(s.label)}<span class="sub">${esc(sub)}</span></button>`
      );
    }
    mapEl.innerHTML = html.join("");
  }

  function renderBar() {
    const r = data.my_reservation;
    const me = data.me || {};
    if (!r && me.suspended_until) {
      barEl.innerHTML = `<div class="txt"><span class="deadline">이용 정지 중</span> · ${me.suspended_until.slice(5, 10)} ${fmtTime(me.suspended_until)}까지 예약할 수 없어요.</div>`;
      return;
    }
    if (!r) {
      barEl.innerHTML = `<div class="txt">빈 좌석을 눌러 예약하세요</div>`;
      return;
    }
    const now = serverNow();
    let txt;
    if (r.status === "reserved") {
      txt = `<strong>${esc(r.seat_label)}</strong> · 예약됨<br>
             <span class="deadline">${fmtRemain(parseTs(r.checkin_deadline) - now)}</span> 안에 좌석 QR로 체크인하세요`;
    } else {
      txt = `<strong>${esc(r.seat_label)}</strong> · 남은 시간 <strong>${fmtRemain(parseTs(r.end_at) - now)}</strong>`;
    }
    barEl.innerHTML = `<div class="txt">${txt}</div><a class="btn small" href="/my">내 자리 관리</a>`;
  }

  function renderAttention() {
    if (!attnBar) return;
    const list = data.me.admin ? data.seats.filter((s) => s.attention) : [];
    attnBar.classList.toggle("hidden", !list.length);
    if (list.length) {
      document.getElementById("attn-text").textContent = `처리 필요 ${list.length}석 · ${list.map((s) => s.label).join(", ")}`;
    }
  }

  async function load() {
    data = await api("GET", "/api/seats", null, { quiet: true });
    syncClock(data.server_time);
    renderMap();
    renderBar();
    renderAttention();
  }

  async function reserve(seat) {
    const p = data.policy;
    const ok = await modal({ title: `${seat.label} 좌석을 예약할까요?`,
      body: `${seat.zone ? seat.zone + " · " : ""}이용 시간 ${fmtMinutes(p.default_use_min)}, ${p.checkin_limit_min}분 안에 체크인 필요`,
      ok: "예약하기" });
    if (!ok) return;
    busy = true;
    try {
      await api("POST", "/api/reservations", { seat_no: seat.no });
      toast(`${seat.label} 좌석을 예약했어요. 좌석 QR로 체크인해 주세요.`, "ok");
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
    if (seat.view === "available") {
      if (data.my_reservation) { toast("이미 예약한 좌석이 있어요. 반납 후 다시 예약해 주세요.", "error"); return; }
      reserve(seat);
      return;
    }
    toast(seat.view === "taken" ? "사용중인 좌석입니다" : "현재 사용할 수 없는 좌석입니다");
  });

  const ticker = poll(load, 3000);
  setInterval(() => { if (data) renderBar(); }, 1000);
})();
