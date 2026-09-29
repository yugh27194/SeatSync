/* /map — 실시간 좌석 지도 (3초 polling) */
(function () {
  "use strict";
  const { api, poll, fmtRemain, syncClock, serverNow, parseTs, toast, modal, esc } = window.SS;
  const mapEl = document.getElementById("seatmap");
  const barEl = document.getElementById("mybar");
  let data = null;
  let busy = false;

  const SUB = { available: "빈자리", taken: "예약(사용중)", unavailable: "사용불가", mine: "내 자리" };

  function fmtMinutes(min) {
    const h = Math.floor(min / 60), m = min % 60;
    return (h ? h + "시간" : "") + (h && m ? " " : "") + (m ? m + "분" : "");
  }

  function renderMap() {
    const g = data.grid;
    mapEl.style.gridTemplateColumns = `repeat(${g.cols}, minmax(56px, 1fr))`;
    mapEl.style.gridTemplateRows = `repeat(${g.rows}, minmax(64px, auto))`;
    const html = [];
    for (const f of data.fixtures) {
      html.push(`<div class="fixture" style="grid-column:${f.x};grid-row:${f.y}">${esc(f.label)}</div>`);
    }
    for (const s of data.seats) {
      const sub = SUB[s.view];
      html.push(
        `<button type="button" class="seat v-${s.view}" data-no="${s.no}"
          style="grid-column:${s.x};grid-row:${s.y}" aria-label="${esc(s.label)} ${sub}">
          ${esc(s.label)}<span class="sub">${sub}</span></button>`
      );
    }
    mapEl.innerHTML = html.join("");
  }

  function renderBar() {
    const r = data.my_reservation;
    const me = data.me || {};
    if (!r && me.suspended_until) {
      barEl.innerHTML = `<div class="txt"><span class="deadline">이용 정지 중</span> · ${me.suspended_until.slice(5, 10)} ${SS.fmtTime(me.suspended_until)}까지 예약할 수 없어요.</div>`;
      return;
    }
    if (!r) {
      barEl.innerHTML = `<div class="txt">빈 좌석을 눌러 예약하세요</div>`;
      return;
    }
    const now = serverNow();
    let txt;
    if (r.status === "reserved") {
      const left = parseTs(r.checkin_deadline) - now;
      txt = `<strong>${esc(r.seat_label)}</strong> · 예약됨<br>
             <span class="deadline">${fmtRemain(left)}</span> 안에 좌석 QR로 체크인하세요`;
    } else {
      txt = `<strong>${esc(r.seat_label)}</strong> · 남은 시간 <strong>${fmtRemain(parseTs(r.end_at) - now)}</strong>`;
    }
    barEl.innerHTML = `<div class="txt">${txt}</div><a class="btn small" href="/my">내 자리 관리</a>`;
  }

  async function load() {
    data = await api("GET", "/api/seats", null, { quiet: true });
    syncClock(data.server_time);
    renderMap();
    renderBar();
  }

  async function reserve(seat) {
    const p = data.policy;
    const body = `이용 시간 ${fmtMinutes(p.default_use_min)}, ${p.checkin_limit_min}분 안에 체크인 필요`;
    const ok = await modal({ title: `${seat.label} 좌석을 예약할까요?`, body, ok: "예약하기" });
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
    if (seat.view === "mine") { location.href = "/my"; return; }
    if (seat.view === "available") {
      if (data.my_reservation) { toast("이미 예약한 좌석이 있어요. 반납 후 다시 예약해 주세요.", "error"); return; }
      reserve(seat);
      return;
    }
    toast(seat.view === "taken" ? "예약(사용중)인 좌석입니다" : "현재 사용할 수 없는 좌석입니다");
  });

  const ticker = poll(load, 3000);
  setInterval(() => { if (data) renderBar(); }, 1000);
})();
