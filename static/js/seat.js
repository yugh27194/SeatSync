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
    if (data.qr_ok === false) n.push(notice("좌석 QR을 다시 스캔해 주세요. (QR 정보가 올바르지 않습니다)", "danger"));
    if (data.offline) n.push(notice("좌석 감지 일시 중단 — 예약 정보를 기준으로 표시합니다.", "warn"));
    notices.innerHTML = n.join("");
  }

  const tokenOk = () => !!token && data.qr_ok === true;

  function render() {
    renderNotices();
    const r = data.my_reservation;
    const mode = data.page_mode;
    // 같은 화면이면 버튼을 다시 그리지 않고 카운트다운만 갱신 (입력 중 깜빡임 방지)
    const key = [mode, r && r.id, r && r.status, r && r.end_at, r && r.can_extend, data.occupied, data.qr_ok].join("|");
    if (key === lastKey) { tick(); return; }
    lastKey = key;

    let h = "";
    if (mode === "mine_checkin") {
      h = `<h2>예약하신 좌석입니다</h2>
        <p>체크인 마감까지 <strong class="deadline" data-cd="checkin"></strong></p>
        ${tokenOk() ? "" : notice("좌석에 붙은 QR을 스캔해야 체크인할 수 있어요.", "warn")}
        <button class="btn big" id="btn-checkin" ${tokenOk() ? "" : "disabled"}>체크인</button>`;
    } else if (mode === "mine_in_use") {
      h = `<h2><span class="pill blue">이용 중</span></h2>
        <div class="countdown" data-cd="end"></div>
        <p class="muted small" style="text-align:center">종료 ${fmtTime(r.end_at)} · 연장 ${r.extend_count}/${r.max_extends}회</p>
        <div class="btn-row">
          <button class="btn" id="btn-extend" ${r.can_extend ? "" : "disabled"}>연장</button>
          <button class="btn secondary" id="btn-return">반납</button>
        </div>
        ${r.can_extend ? "" : `<p class="muted small">${esc(r.extend_reason || "")}</p>`}
        <p class="small"><a href="/my">내 자리 관리</a></p>`;
    } else if (mode === "reserve_now") {
      const p = data.policy;
      if (r) {
        h = `<h2>이 좌석을 배정받아 주세요</h2>
          <p>현재 <strong>${esc(r.seat_label)}</strong> 좌석을 ${r.status === "reserved" ? "예약" : "이용"} 중입니다.</p>
          <button class="btn big" id="btn-switch">반납하고 이 좌석 예약</button>`;
      } else {
        const how = tokenOk()
          ? "예약과 동시에 체크인됩니다."
          : `좌석 QR 없이 예약하면 ${p.checkin_limit_min}분 안에 QR로 체크인해야 해요.`;
        h = `<h2>이 좌석을 배정받아 주세요</h2>
          <p class="muted">앉아 계신다면 지금 바로 예약해 주세요. ${how}</p>
          ${data.occupied && !tokenOk() ? notice("현재 다른 이용자가 앉아 있는 좌석입니다. 본인이라면 좌석 QR을 스캔해 주세요.", "warn") : ""}
          <button class="btn big" id="btn-reserve" ${data.occupied && !tokenOk() ? "disabled" : ""}>바로 예약하기</button>`;
      }
    } else if (mode === "reserved_by_other") {
      h = `<h2>예약된 좌석입니다</h2>
        <p class="muted">예약자라면 본인 계정으로 로그인하세요.</p>
        <p class="muted small">예약한 좌석에 다른 분이 앉아 계신가요? 관리자에게 알려 주세요.</p>
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
      el.textContent = el.dataset.cd === "end" ? fmtClock(left) : fmtRemain(left);
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
    on("btn-checkin", () => act(() => api("POST", `/api/reservations/${r.id}/checkin`, { qr_token: token }), "체크인했어요. 좋은 시간 되세요!"));
    on("btn-extend", () => act(() => api("POST", `/api/reservations/${r.id}/extend`), "이용 시간을 연장했어요."));
    on("btn-return", async () => {
      if (await modal({ title: "반납할까요?", body: `${r.seat_label} 좌석을 반납합니다.`, ok: "반납", danger: true }))
        act(() => api("POST", `/api/reservations/${r.id}/return`), "반납했어요.");
    });
    on("btn-reserve", () => act(
      () => api("POST", "/api/reservations", token ? { seat_no: seatNo, qr_token: token } : { seat_no: seatNo }),
      tokenOk() ? "예약·체크인 완료!" : "예약했어요. 좌석 QR로 체크인해 주세요."));
    on("btn-switch", async () => {
      if (!(await modal({ title: "좌석을 바꿀까요?", body: `${r.seat_label} 좌석을 반납하고 이 좌석을 예약합니다.`, ok: "바꾸기" }))) return;
      act(async () => {
        await api("POST", `/api/reservations/${r.id}/return`);
        await api("POST", "/api/reservations", token ? { seat_no: seatNo, qr_token: token } : { seat_no: seatNo });
      }, "좌석을 변경했어요.");
    });
    on("btn-call", async () => {
      const memo = await modal({ title: "관리자 호출", body: "상황을 간단히 적어 주세요. (선택)", ok: "호출",
        input: { placeholder: "예: 제 예약석에 다른 분이 앉아 계세요" } });
      if (memo === false) return;
      act(async () => {
        const res = await api("POST", "/api/calls", { seat_no: seatNo, memo });
        if (res.duplicate) toast("방금 호출했어요. 관리자가 확인 중입니다.");
      }, "관리자를 호출했어요.");
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
