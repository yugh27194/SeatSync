/* /seat/<no>?t=<qr_token> — 좌석 QR 도착 페이지 */
(function () {
  "use strict";
  const { api, poll, fmtRemain, fmtClock, fmtTime, syncClock, serverNow, parseTs, toast, modal, esc } = window.SS;
  const root = document.getElementById("seat-root");
  const seatNo = Number(root.dataset.no);
  const token = root.dataset.token || "";
  const panel = document.getElementById("panel");
  const notices = document.getElementById("notices");
  let data = null;
  let lastKey = "";

  function notice(html, kind) { return `<div class="notice ${kind || ""}">${html}</div>`; }

  function renderNotices() {
    const n = [];
    if (data.qr_ok === false) n.push(notice("QR을 인식하지 못했어요. 좌석 QR을 다시 찍어 주세요.", "danger"));
    if (data.me && data.me.suspended_until && !data.my_reservation)
      n.push(notice(`이용 정지 중이에요. ${data.me.suspended_until.slice(5, 10)} ${fmtTime(data.me.suspended_until)}까지 예약할 수 없어요.`, "danger"));
    if (data.unavailable && data.page_mode !== "unavailable")
      n.push(notice("이 좌석은 지금 사용할 수 없어요. 관리자에게 문의해 주세요.", "warn"));
    notices.innerHTML = n.join("");
  }

  const tokenOk = () => !!token && data.qr_ok === true;

  function render() {
    renderNotices();
    const r = data.my_reservation;
    const mode = data.page_mode;
    // 같은 화면이면 버튼을 다시 그리지 않고 카운트다운만 갱신 (입력 중 깜빡임 방지)
    const key = [mode, r && r.id, r && r.status, r && r.end_at, r && r.can_extend, data.occupied, data.qr_ok, data.unavailable].join("|");
    if (key === lastKey) { tick(); return; }
    lastKey = key;

    let h = "";
    if (mode === "mine_checkin") {
      h = `<h2>예약한 좌석이에요</h2>
        <p>체크인 마감까지 <strong class="deadline" data-cd="checkin"></strong></p>
        ${tokenOk() ? "" : notice("좌석에 붙은 QR을 찍어야 체크인할 수 있어요.", "warn")}
        <button class="btn big" id="btn-checkin" ${tokenOk() ? "" : "disabled"}>체크인</button>
        <button class="btn secondary block" id="btn-taken" style="margin-top:10px">내 자리에 다른 사람이 앉아 있어요</button>`;
    } else if (mode === "mine_in_use") {
      h = `<h2><span class="pill blue">이용 중</span></h2>
        <div class="countdown" data-cd="end"></div>
        <p class="muted small" style="text-align:center">종료 ${fmtTime(r.end_at)} · 연장 ${r.extend_count}/${r.max_extends}회</p>
        <div class="btn-row">
          <button class="btn" id="btn-extend" ${r.can_extend ? "" : "disabled"}>연장</button>
          <button class="btn secondary" id="btn-return">반납</button>
        </div>
        ${r.can_extend ? "" : `<p class="muted small">${esc(r.extend_reason || "")}</p>`}
        <button class="btn secondary block" id="btn-taken" style="margin-top:10px">내 자리에 다른 사람이 앉아 있어요</button>
        <p class="small"><a href="/my">내 자리 관리</a></p>`;
    } else if (mode === "reserve_now") {
      const p = data.policy;
      if (r) {
        // 다른 좌석을 예약한 사람이 이 좌석 QR을 찍음 → 잘못 앉았을 수 있다
        h = `<h2>예약한 좌석은 <strong>${esc(r.seat_label)}</strong>이에요</h2>
          ${notice(`지금 찍은 QR은 <b>${esc(data.label)}</b> 좌석이에요. <b>${esc(r.seat_label)}</b> 좌석의 QR을 찍어 주세요.`, "warn")}
          <p class="muted small">이 좌석으로 바꾸면 ${esc(r.seat_label)} ${r.status === "reserved" ? "예약은 취소" : "이용은 반납"}돼요.</p>
          <button class="btn big" id="btn-switch">${esc(r.seat_label)} → ${esc(data.label)}(으)로 바꾸기</button>`;
      } else {
        const how = tokenOk()
          ? "예약하면 바로 체크인돼요."
          : `예약 후 ${SS.fmtMin(p.checkin_limit_min)} 안에 좌석 QR로 체크인해 주세요.`;
        h = `<h2>이 좌석을 예약할까요?</h2>
          <p class="muted">${how}</p>
          ${data.occupied && !tokenOk() ? notice("다른 사용자가 사용 중인 좌석입니다.", "warn") : ""}
          <button class="btn big" id="btn-reserve" ${data.occupied && !tokenOk() ? "disabled" : ""}>바로 예약하기</button>`;
      }
    } else if (mode === "unavailable") {
      h = `<h2><span class="pill">사용불가</span></h2>
        <p>사용할 수 없는 좌석입니다. 관리자에게 문의해 주세요.</p>`;
    } else if (mode === "reserved_by_other") {
      h = `<h2>다른 사용자가 예약한 좌석입니다</h2>
        <p class="muted">빈자리는 좌석 지도에서 찾을 수 있어요. 문제가 있으면 관리자를 불러 주세요.</p>
        <button class="btn secondary block" id="btn-call">관리자 호출</button>`;
    }
    panel.innerHTML = h;
    bind();
    tick();
  }

  function tick() {
    if (!data || !data.my_reservation) return;
    const r = data.my_reservation, now = serverNow();
    panel.querySelectorAll("[data-cd]").forEach((el) => {
      const left = (el.dataset.cd === "checkin" ? parseTs(r.checkin_deadline) : parseTs(r.end_at)) - now;
      el.textContent = el.dataset.cd === "end" ? fmtClock(left) : (left > 0 ? fmtRemain(left) : "시간 지남 · 지금 체크인해 주세요");
      el.classList.toggle("low", left < 600);
    });
  }

  async function act(fn, okMsg) {
    try { await fn(); if (okMsg) toast(okMsg, "ok"); } catch (e) { /* 토스트 표시됨 */ }
    lastKey = "";
    await ticker.refresh();
  }

  function bind() {
    const r = data.my_reservation;
    const on = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = fn; };
    on("btn-taken", () => SS.reportSeatTaken(seatNo, r.seat_label));
    on("btn-checkin", () => act(() => api("POST", `/api/reservations/${r.id}/checkin`, { qr_token: token }), "체크인 완료!"));
    on("btn-extend", () => act(() => api("POST", `/api/reservations/${r.id}/extend`), "이용 시간을 연장했어요."));
    on("btn-return", async () => {
      if (await modal({ title: `${r.seat_label} 좌석을 반납할까요?`, ok: "반납", danger: true }))
        act(() => api("POST", `/api/reservations/${r.id}/return`), "반납했어요.");
    });
    on("btn-reserve", () => act(
      () => api("POST", "/api/reservations", token ? { seat_no: seatNo, qr_token: token } : { seat_no: seatNo }),
      tokenOk() ? "예약·체크인 완료!" : "예약 완료! 좌석 QR로 체크인해 주세요."));
    on("btn-switch", async () => {
      if (!(await modal({ title: "좌석을 바꿀까요?", body: `${r.seat_label} → ${data.label}`, ok: "바꾸기" }))) return;
      act(async () => {
        await api("POST", `/api/reservations/${r.id}/return`);
        await api("POST", "/api/reservations", token ? { seat_no: seatNo, qr_token: token } : { seat_no: seatNo });
      }, "좌석을 바꿨어요.");
    });
    on("btn-call", async () => {
      const memo = await modal({ title: "관리자 호출", ok: "호출",
        input: { placeholder: "상황을 적어 주세요 (선택)" } });
      if (memo === false) return;
      act(async () => {
        const res = await api("POST", "/api/calls", { seat_no: seatNo, memo });
        if (res.duplicate) toast("방금 호출했어요. 잠시만 기다려 주세요.");
      }, "관리자를 불렀어요.");
    });
  }

  async function load() {
    const q = token ? "?t=" + encodeURIComponent(token) : "";
    data = await api("GET", `/api/seats/${seatNo}${q}`, null, { quiet: true });
    syncClock(data.server_time);
    render();
  }

  const ticker = poll(load, 3000);
  setInterval(tick, 1000);
})();
