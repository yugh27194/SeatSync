/* /my — 내 자리 관리. 카운트다운은 1초 로컬 갱신, 30초마다 서버 재동기화 */
(function () {
  "use strict";
  const { api, fmtRemain, fmtClock, fmtTime, syncClock, serverNow, parseTs, toast, modal, esc } = window.SS;
  const root = document.getElementById("my-root");
  let r = null, me = {}, st = null, lastKey = null;

  function render() {
    if (!r) {
      root.innerHTML = `<div class="card seat-hero"><h1>현재 예약이 없어요</h1>
        ${me.suspended_until ? `<div class="notice danger">이용 정지 중입니다. ${me.suspended_until.slice(5, 10)} ${fmtTime(me.suspended_until)}까지 예약할 수 없어요.</div>` : ""}
        <p class="muted">좌석 지도에서 빈자리를 누르거나, 좌석의 QR을 스캔해 예약하세요.</p>
        <a class="btn" href="/map">좌석 지도로</a></div>`;
      return;
    }
    const reserved = r.status === "reserved";
    root.innerHTML = `
      <div class="card seat-hero">
        <div class="label">${esc(r.seat_label)}</div>
        <span class="pill ${reserved ? "" : "blue"}">${reserved ? "예약됨 · 체크인 전" : "이용 중"}</span>
      </div>
      ${st ? `<div class="own-status lv-${st.level} big">⚠ <b>${esc(st.label)}</b> · ${esc(st.message)}</div>` : ""}
      <div class="card">
        ${reserved
          ? `<p style="text-align:center;margin:0">체크인 마감까지</p>
             <div class="countdown" id="cd"></div>
             <div class="notice warn">좌석에 붙은 QR을 스캔해 체크인하세요. 마감까지 체크인하지 않으면 '미입실'로 표시되고 관리자가 예약을 취소할 수 있어요.</div>`
          : `<p style="text-align:center;margin:0">남은 시간</p><div class="countdown" id="cd"></div>`}
        <dl class="kv">
          <dt>시작</dt><dd>${fmtTime(r.start_at)}</dd>
          <dt>종료</dt><dd>${fmtTime(r.end_at)}</dd>
          ${r.checked_in_at ? `<dt>체크인</dt><dd>${fmtTime(r.checked_in_at)}</dd>` : ""}
          <dt>연장</dt><dd>${r.extend_count} / ${r.max_extends}회</dd>
          ${me.warnings ? `<dt>누적 경고</dt><dd style="color:var(--danger)">${me.warnings}회</dd>` : ""}
        </dl>
      </div>
      <div class="card">
        <div class="btn-row">
          <button class="btn" id="btn-extend" ${r.can_extend ? "" : "disabled"}>연장</button>
          <button class="btn secondary" id="btn-return">${reserved ? "예약 취소" : "조기 반납"}</button>
        </div>
        ${r.can_extend ? "" : `<p class="muted small" style="margin:8px 0 0">${esc(r.extend_reason || "")}</p>`}
        <hr style="border:0;border-top:1px solid var(--line);margin:14px 0">
        <button class="btn danger block" id="btn-taken">내 자리에 다른 사람이 앉아 있어요</button>
        <button class="btn secondary block" id="btn-call" style="margin-top:8px">관리자 호출 (그 밖의 문제)</button>
        <p class="muted small" style="margin:6px 0 0">예약한 좌석에 다른 분이 앉아 있으면 위 버튼을 눌러 주세요. 관리자 화면에 바로 알림이 뜹니다.</p>
        <p class="small" style="margin:10px 0 0"><a href="/history">내 이용 기록 보기 →</a></p>
      </div>`;
    bind();
    tick();
  }

  function tick() {
    const el = document.getElementById("cd");
    if (!el || !r) return;
    const left = (r.status === "reserved" ? parseTs(r.checkin_deadline) : parseTs(r.end_at)) - serverNow();
    const overdue = r.status === "reserved" && left <= 0;  // 체크인 시간 지남: 예약은 유지(미입실 표시)
    el.textContent = overdue ? "시간 지남" : fmtClock(left);
    el.classList.toggle("low", left < 600);
    if (left <= 0 && !overdue) sync();
  }

  function bind() {
    document.getElementById("btn-extend").onclick = async () => {
      try { await api("POST", `/api/reservations/${r.id}/extend`); toast("이용 시간을 연장했어요.", "ok"); } catch (e) { /* 토스트 */ }
      sync();
    };
    document.getElementById("btn-return").onclick = async () => {
      const reserved = r.status === "reserved";
      const ok = await modal({ title: reserved ? "예약을 취소할까요?" : "조기 반납할까요?",
        body: `${r.seat_label} 좌석을 ${reserved ? "취소" : "반납"}합니다.`, ok: reserved ? "예약 취소" : "반납", danger: true });
      if (!ok) return;
      try { await api("POST", `/api/reservations/${r.id}/return`); toast(reserved ? "예약을 취소했어요." : "반납했어요.", "ok"); } catch (e) { /* 토스트 */ }
      sync();
    };
    document.getElementById("btn-taken").onclick = () => SS.reportSeatTaken(r.seat_no, r.seat_label);
    document.getElementById("btn-call").onclick = async () => {
      const memo = await modal({ title: "관리자 호출", body: "상황을 간단히 적어 주세요.", ok: "호출",
        input: { placeholder: "예: 제 예약석에 다른 분이 앉아 계세요" } });
      if (memo === false) return;
      try {
        const res = await api("POST", "/api/calls", { seat_no: r.seat_no, memo });
        toast(res.duplicate ? "방금 호출했어요. 관리자가 확인 중입니다." : "관리자를 호출했어요.", "ok");
      } catch (e) { /* 토스트 */ }
    };
  }

  let syncing = false;
  async function sync() {
    if (syncing) return;
    syncing = true;
    try {
      const d = await api("GET", "/api/seats", null, { quiet: true });
      syncClock(d.server_time);
      r = d.my_reservation;
      me = d.me || {};
      st = d.my_status;
      // 남은 초만 바뀐 경우엔 다시 그리지 않는다(버튼·모달 깜빡임 방지)
      const key = JSON.stringify([r && { ...r, remaining_sec: 0 }, me, st && st.detail]);
      if (key !== lastKey) { lastKey = key; render(); }
    } catch (e) { /* 다음 주기 재시도 */ }
    finally { syncing = false; }
  }

  sync();
  setInterval(tick, 1000);
  setInterval(() => { if (document.visibilityState !== "hidden") sync(); }, 10000);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") sync(); });
})();
