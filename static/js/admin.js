/* /admin — 관리자 대시보드 (3초 polling) */
(function () {
  "use strict";
  const { api, poll, fmtRemain, fmtTime, syncClock, toast, modal, esc } = window.SS;

  const SHORT = {
    AVAILABLE: "빈자리", TEMP_OCCUPIED: "임시 점유", UNAUTHORIZED: "무단 사용", ITEM_ONLY: "물품 점유",
    RESERVED: "예약됨", AWAITING_CHECKIN: "체크인 대기", IN_USE: "정상 이용", AWAY_WITH_ITEM: "이석(짐)",
    HOARDING: "사석화", AWAY_EMPTY: "이석(빈)", RETURN_DUE: "반납 대상", OFFLINE: "감지 끊김",
  };
  const NEXT = {
    TEMP_OCCUPIED: "무단 사용", AWAY_WITH_ITEM: "사석화", AWAY_EMPTY: "반납 대상",
    RESERVED: "미입실", AWAITING_CHECKIN: "미입실",
  };
  const OCC = { person: "사람", item: "짐", empty: "비어 있음" };
  const RES_STATUS = { reserved: "예약(입실 전)", in_use: "이용 중", returned: "반납", cancelled: "취소",
    expired: "만료", no_show: "미입실", force_returned: "강제 반납" };

  const $ = (id) => document.getElementById(id);
  let seats = null, alerts = [], selected = null;
  let seen = null; // 이미 본 알림 id
  let soundOn = false, audioCtx = null;
  try { soundOn = localStorage.getItem("ss-sound") === "1"; } catch (e) { /* 저장소 없음 */ }

  // ------------------------------------------------ 소리
  function updateSoundBtn() { $("sound-toggle").textContent = soundOn ? "🔔 소리 끄기" : "🔕 소리 켜기"; }
  $("sound-toggle").onclick = () => {
    soundOn = !soundOn;
    try { localStorage.setItem("ss-sound", soundOn ? "1" : "0"); } catch (e) { /* 무시 */ }
    if (soundOn) { ensureAudio(); beep(); }
    updateSoundBtn();
  };
  function ensureAudio() {
    if (!audioCtx) { try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { return null; } }
    if (audioCtx.state === "suspended") audioCtx.resume();
    return audioCtx;
  }
  // 브라우저는 사용자 상호작용 이후에만 소리를 허용하므로, 첫 클릭 때 오디오를 깨운다.
  document.addEventListener("click", () => { if (soundOn) ensureAudio(); }, { once: true });
  function beep() {
    if (!soundOn || !audioCtx || audioCtx.state !== "running") return;
    [0, 0.22].forEach((t) => {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.type = "sine"; o.frequency.value = 880;
      g.gain.setValueAtTime(0.0001, audioCtx.currentTime + t);
      g.gain.exponentialRampToValueAtTime(0.25, audioCtx.currentTime + t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + t + 0.18);
      o.connect(g).connect(audioCtx.destination);
      o.start(audioCtx.currentTime + t); o.stop(audioCtx.currentTime + t + 0.2);
    });
  }
  updateSoundBtn();

  // ------------------------------------------------ 배너
  let bannerTimer = null;
  function showBanner(msg) {
    $("banner-msg").textContent = msg;
    $("banner").classList.remove("hidden");
    clearTimeout(bannerTimer);
    bannerTimer = setTimeout(() => $("banner").classList.add("hidden"), 20000);
  }
  $("banner-close").onclick = () => $("banner").classList.add("hidden");

  function checkNewAlerts() {
    const ids = new Set(alerts.map((a) => a.id));
    if (seen === null) { seen = ids; return; } // 첫 로드는 기준점만 잡는다
    const fresh = alerts.filter((a) => !seen.has(a.id));
    fresh.forEach((a) => seen.add(a.id));
    if (fresh.length) {
      showBanner(fresh.map((a) => `${a.seat_label} ${a.type_label} 발생`).join(" · "));
      beep();
    }
  }

  // ------------------------------------------------ 렌더링
  function tileText(s) {
    if (s.state === "AVAILABLE" || s.state === "OFFLINE") return SHORT[s.state];
    if (s.deadline_sec != null && NEXT[s.state]) return `${SHORT[s.state]} · ${fmtRemain(s.deadline_sec)} 뒤 ${NEXT[s.state]}`;
    return `${SHORT[s.state]} · ${fmtRemain(s.elapsed_sec)}`;
  }

  function renderChips() {
    $("chips").innerHTML = Object.keys(seats.summary).map((st) => {
      const n = seats.summary[st];
      return `<span class="chip st-${st}${n ? "" : " zero"}">${esc(seats.state_labels[st])} <b>${n}</b></span>`;
    }).join("");
  }

  function renderMap() {
    const el = $("admin-map"), g = seats.grid;
    el.style.gridTemplateColumns = `repeat(${g.cols}, minmax(52px, 1fr))`;
    el.style.gridTemplateRows = `repeat(${g.rows}, minmax(76px, auto))`;
    const html = seats.fixtures.map((f) =>
      `<div class="fixture" style="grid-column:${f.x};grid-row:${f.y}">${esc(f.label)}</div>`);
    for (const s of seats.seats) {
      html.push(`<button type="button" class="seat st-${s.state}${selected === s.no ? " selected" : ""}" data-no="${s.no}"
        style="grid-column:${s.x};grid-row:${s.y}" title="${esc(s.state_label)}">
        ${esc(s.label)}<span class="sub">${esc(tileText(s))}</span></button>`);
    }
    el.innerHTML = html.join("");
  }

  function resLine(r) {
    if (!r) return `<span class="muted">예약 없음</span>`;
    return `${esc(r.user_name)} (${esc(r.student_no)}) · ${RES_STATUS[r.status] || r.status}<br>
      <span class="muted small">${fmtTime(r.start_at)}~${fmtTime(r.end_at)}${r.checked_in_at ? " · 체크인 " + fmtTime(r.checked_in_at) : ""}</span>`;
  }

  function detLine(s) {
    if (!s.occupancy) return `<span class="muted">수신 없음</span>`;
    return `${OCC[s.occupancy]} <span class="muted small">(${fmtRemain(s.detection_age_sec)} 전 수신)</span>`;
  }

  function renderDetail() {
    const el = $("detail");
    const s = seats.seats.find((x) => x.no === selected);
    if (!s) { el.innerHTML = `<h2>좌석 상세</h2><p class="empty">좌석을 선택하세요.</p>`; return; }
    const r = s.reservation;
    el.innerHTML = `<h2>${esc(s.label)} <span class="statetag st-${s.state}">${esc(s.state_label)}</span></h2>
      <dl class="kv">
        <dt>상태 시작</dt><dd>${fmtTime(s.since)} (${fmtRemain(s.elapsed_sec)} 경과)</dd>
        ${s.deadline ? `<dt>다음 전이</dt><dd>${fmtTime(s.deadline)} ${NEXT[s.state] || ""} (${fmtRemain(s.deadline_sec)} 뒤)</dd>` : ""}
        <dt>예약자</dt><dd>${r ? `${esc(r.user_name)} · ${esc(r.student_no)}` : "없음"}</dd>
        ${r ? `<dt>예약 상태</dt><dd>${RES_STATUS[r.status]}</dd>
               <dt>예약 시각</dt><dd>${fmtTime(r.start_at)} ~ ${fmtTime(r.end_at)}</dd>
               <dt>체크인</dt><dd>${r.checked_in_at ? fmtTime(r.checked_in_at) : "안 함"}</dd>` : ""}
        <dt>감지 상태</dt><dd>${s.occupancy ? OCC[s.occupancy] : "없음"}${s.confidence != null ? ` <span class="muted small">(신뢰도 ${Math.round(s.confidence * 100)}%)</span>` : ""}</dd>
        <dt>마지막 수신</dt><dd>${s.detection_updated_at ? `${fmtTime(s.detection_updated_at)} (${fmtRemain(s.detection_age_sec)} 전)` : "없음"}</dd>
      </dl>
      ${r ? `<div class="btn-row" style="margin-top:12px"><button class="btn danger small" data-force="${r.id}" data-label="${esc(s.label)}">강제 반납</button></div>` : ""}`;
  }

  function renderAlerts() {
    $("alert-count").textContent = alerts.length;
    if (!alerts.length) { $("alerts").innerHTML = `<li class="muted small">미해결 문제가 없습니다.</li>`; return; }
    $("alerts").innerHTML = alerts.map((a) => {
      const r = a.reservation;
      return `<li class="alert-item">
        <div class="head"><span class="seatlbl">${esc(a.seat_label)}</span>
          <span class="badge t-${a.type}">${esc(a.type_label)}</span>
          <span class="muted small">${fmtRemain(a.elapsed_sec)} 전 · ${fmtTime(a.created_at)}</span></div>
        <div class="meta">${r ? `예약자 ${esc(r.user_name)} (${esc(r.student_no)}) · ${RES_STATUS[r.status] || ""}` : "예약 없음"}
          ${a.caller ? ` · 호출자 ${esc(a.caller.name)}` : ""}</div>
        ${a.memo ? `<div class="memo">${esc(a.memo)}</div>` : ""}
        <div class="btn-row">
          <button class="btn small secondary" data-resolve="${a.id}">처리 완료</button>
          ${a.active_reservation_id ? `<button class="btn small danger" data-force="${a.active_reservation_id}" data-label="${esc(a.seat_label)}">강제 반납</button>` : ""}
        </div></li>`;
    }).join("");
  }

  function renderRecon() {
    $("recon").innerHTML = seats.seats.map((s) => `<tr class="${s.alert_type ? "mismatch" : ""}">
      <td><b>${esc(s.label)}</b></td>
      <td>${resLine(s.reservation)}</td>
      <td>${detLine(s)}</td>
      <td><span class="statetag st-${s.state}">${esc(s.state_label)}</span></td></tr>`).join("");
  }

  async function load() {
    const [s, a] = await Promise.all([
      api("GET", "/api/admin/seats", null, { quiet: true }),
      api("GET", "/api/admin/alerts?open=1", null, { quiet: true }),
    ]);
    seats = s; alerts = a.alerts;
    syncClock(s.server_time);
    renderChips(); renderMap(); renderDetail(); renderAlerts(); renderRecon();
    checkNewAlerts();
    $("updated").textContent = "갱신 " + fmtTime(s.server_time) + ":" + s.server_time.slice(17, 19);
  }

  // ------------------------------------------------ 동작
  $("admin-map").addEventListener("click", (e) => {
    const b = e.target.closest(".seat");
    if (!b) return;
    selected = Number(b.dataset.no);
    renderMap(); renderDetail();
    if (window.innerWidth < 960) $("detail").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  document.addEventListener("click", async (e) => {
    const rb = e.target.closest("[data-resolve]");
    if (rb) {
      rb.disabled = true;
      try { await api("POST", `/api/admin/alerts/${rb.dataset.resolve}/resolve`); toast("처리 완료했습니다.", "ok"); }
      catch (err) { /* 토스트 표시됨 */ }
      await ticker.refresh();
      return;
    }
    const fb = e.target.closest("[data-force]");
    if (fb) {
      const ok = await modal({ title: "강제 반납", body: `${fb.dataset.label} 좌석의 예약을 강제 반납할까요?\n해당 좌석의 미해결 알림도 함께 처리됩니다.`,
        ok: "강제 반납", danger: true });
      if (!ok) return;
      try { await api("POST", `/api/admin/reservations/${fb.dataset.force}/force-return`); toast("강제 반납했습니다.", "ok"); }
      catch (err) { /* 토스트 표시됨 */ }
      await ticker.refresh();
    }
  });

  const ticker = poll(load, 3000);

  // ------------------------------------------------ 통계 (시간대별 사석화율)
  const dateEl = $("stats-date");
  function today() {
    const d = new Date(Date.now() + 9 * 3600 * 1000); // KST
    return d.toISOString().slice(0, 10);
  }
  dateEl.value = today();
  async function loadStats() {
    let d;
    try { d = await api("GET", "/api/admin/stats?date=" + dateEl.value, null, { quiet: true }); }
    catch (e) { return; }
    const max = Math.max(0.0001, ...d.hours.map((h) => h.hoarding_rate));
    $("chart").innerHTML = d.hours.map((h) => {
      const pct = Math.round(h.hoarding_rate * 100);
      const title = `${h.hour}시 · 사석화율 ${pct}% · 정상 이용 ${h.in_use_min}분 · 사석화 ${h.hoarding_min}분 · 무단 사용 ${h.unauthorized_min}분`;
      return `<div class="col" title="${title}"><span class="val">${pct ? pct + "%" : ""}</span>
        <div class="bar${pct ? "" : " zero"}" style="height:${Math.max(1, (h.hoarding_rate / max) * 85)}%"></div></div>`;
    }).join("");
    $("chart-x").innerHTML = d.hours.map((h) => `<span>${h.hour}</span>`).join("");
    const tot = d.hours.reduce((a, h) => ({ u: a.u + h.in_use_min, h: a.h + h.hoarding_min, x: a.x + h.unauthorized_min }), { u: 0, h: 0, x: 0 });
    $("stats-note").textContent = `${d.date} 합계 — 정상 이용 ${Math.round(tot.u)}분 · 사석화 ${Math.round(tot.h)}분 · 무단 사용 ${Math.round(tot.x)}분 (좌석·분 기준, 막대에 마우스를 올리면 상세)`;
  }
  dateEl.onchange = loadStats;
  loadStats();
  setInterval(() => { if (document.visibilityState !== "hidden") loadStats(); }, 60000);
})();
