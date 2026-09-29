/* /admin — 관리자 대시보드: 3상태 지도 + 예약 대조 + 상황별 조치 (3초 polling) */
(function () {
  "use strict";
  const { api, poll, fmtRemain, fmtTime, syncClock, toast, modal, esc } = window.SS;
  const $ = (id) => document.getElementById(id);

  const ACTUAL = { empty: "빈자리", occupied: "사용중", unavailable: "사용불가" };
  const SOURCE = { manual: "관리자 지정", camera: "카메라", checkin: "QR 체크인", return: "반납", seed: "초기 배분" };
  const RES_STATUS = { reserved: "예약(입실 전)", in_use: "이용 중" };

  let data = null, alerts = [], users = [], selected = null;
  let seen = null;
  let soundOn = false, audioCtx = null;
  try { soundOn = localStorage.getItem("ss-sound") === "1"; } catch (e) { /* 저장소 없음 */ }

  // ------------------------------------------------ 소리·배너
  function updateSoundBtn() { $("sound-toggle").textContent = soundOn ? "🔔 소리 끄기" : "🔕 소리 켜기"; }
  function ensureAudio() {
    if (!audioCtx) { try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { return; } }
    if (audioCtx.state === "suspended") audioCtx.resume();
  }
  function beep() {
    if (!soundOn || !audioCtx || audioCtx.state !== "running") return;
    [0, 0.22].forEach((t) => {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.frequency.value = 880;
      g.gain.setValueAtTime(0.0001, audioCtx.currentTime + t);
      g.gain.exponentialRampToValueAtTime(0.25, audioCtx.currentTime + t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + t + 0.18);
      o.connect(g).connect(audioCtx.destination);
      o.start(audioCtx.currentTime + t); o.stop(audioCtx.currentTime + t + 0.2);
    });
  }
  $("sound-toggle").onclick = () => {
    soundOn = !soundOn;
    try { localStorage.setItem("ss-sound", soundOn ? "1" : "0"); } catch (e) { /* 무시 */ }
    if (soundOn) { ensureAudio(); beep(); }
    updateSoundBtn();
  };
  document.addEventListener("click", () => { if (soundOn) ensureAudio(); }, { once: true });
  updateSoundBtn();

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
    if (seen === null) { seen = ids; return; }
    const fresh = alerts.filter((a) => !seen.has(a.id));
    fresh.forEach((a) => seen.add(a.id));
    if (fresh.length) { showBanner(fresh.map((a) => `${a.seat_label} ${a.type_label}`).join(" · ") + " 발생"); beep(); }
  }

  // ------------------------------------------------ 헬퍼
  const seatByNo = (no) => data && data.seats.find((s) => s.no === Number(no));
  const userLine = (u) => u ? `${esc(u.name)} <span class="muted small">(${esc(u.student_no)})</span>` : "";
  function warnBadge(u) {
    if (!u) return "";
    const hot = u.warnings >= data.settings.warning_limit;
    return `<span class="warnings${hot ? " hot" : ""}">경고 ${u.warnings}</span>` +
      (u.suspended_until ? ` <span class="badge t-away">정지</span>` : "");
  }
  function btn(act, label, attrs, cls) {
    const a = Object.entries(attrs || {}).map(([k, v]) => `data-${k}="${esc(v)}"`).join(" ");
    return `<button type="button" class="btn small ${cls || "secondary"}" data-act="${act}" ${a}>${label}</button>`;
  }
  function tileSub(s) {
    const r = s.reservation;
    if (s.situation !== "ok") return `<span class="warn-tag">⚠ ${esc(s.situation_label)}</span>`;
    if (s.seat_state === "unavailable") return esc(s.actual_note || "사용불가");
    if (!r) return s.seat_state === "available" ? "빈자리" : esc(s.seat_state_label);
    const extra = s.note ? ` · ${s.note}` : "";
    return `${esc(r.user.name)}${extra}`;
  }

  // 상황별 권장 조치
  function recommended(sit, s, alertId) {
    const r = s.reservation, lbl = s.label;
    switch (sit) {
      case "unauthorized":
        return btn("assign", "현장 배정", { seat: s.no, checkin: 1 }, "") +
          btn("leave", "퇴실 안내 완료", { seat: s.no, label: lbl });
      case "no_checkin":
        return r ? btn("checkin", "대리 체크인", { res: r.id, label: lbl }, "") +
          btn("move", "예약자 다른 좌석으로", { res: r.id, label: lbl }) : "";
      case "away":
        return r ? btn("force", "강제 반납", { res: r.id, label: lbl }, "danger") +
          btn("warn", "예약자 경고", { user: r.user.id, name: r.user.name, alert: alertId || "" }) : "";
      case "seat_unavailable":
        return r ? btn("move", "다른 좌석으로 이동", { res: r.id, label: lbl }, "") +
          btn("force", "예약 취소", { res: r.id, label: lbl }, "danger") : "";
      default:
        return "";
    }
  }

  // ------------------------------------------------ 렌더링
  function renderChips() {
    const sm = data.summary;
    $("chips").innerHTML =
      `<span class="chip st-available">빈자리 <b>${sm.available}</b></span>` +
      `<span class="chip st-in_use">예약(사용중) <b>${sm.in_use}</b></span>` +
      `<span class="chip st-unavailable">사용불가 <b>${sm.unavailable}</b></span>` +
      `<span class="chip st-issue${sm.issues ? "" : " zero"}">⚠ 확인 필요 <b>${sm.issues}</b></span>`;
  }

  function renderMap() {
    const el = $("admin-map"), g = data.grid;
    el.style.gridTemplateColumns = `repeat(${g.cols}, minmax(52px, 1fr))`;
    el.style.gridTemplateRows = `repeat(${g.rows}, minmax(76px, auto))`;
    const html = data.fixtures.map((f) => `<div class="fixture" style="grid-column:${f.x};grid-row:${f.y}">${esc(f.label)}</div>`);
    for (const s of data.seats) {
      html.push(`<button type="button" class="seat st-${s.seat_state}${s.situation !== "ok" ? " issue" : ""}${selected === s.no ? " selected" : ""}"
        data-no="${s.no}" style="grid-column:${s.x};grid-row:${s.y}" title="${esc(s.seat_state_label)} · ${esc(s.situation_label)}">
        ${esc(s.label)}<span class="sub">${tileSub(s)}</span></button>`);
    }
    el.innerHTML = html.join("");
  }

  function renderDetail() {
    const el = $("detail");
    const s = seatByNo(selected);
    if (!s) { el.innerHTML = `<h2>좌석 상세 · 조치</h2><p class="empty">좌석을 선택하세요.</p>`; return; }
    const r = s.reservation;
    const issue = s.situation !== "ok";
    const seg = ["empty", "occupied", "unavailable"].map((k) =>
      `<button type="button" class="s-${k}${s.actual === k ? " on" : ""}" data-act="state" data-seat="${s.no}" data-state="${k}">${ACTUAL[k]}</button>`).join("");

    let sitHtml;
    if (issue) {
      sitHtml = `<div class="situation-box"><b>⚠ ${esc(s.situation_label)}</b> · ${fmtRemain(s.elapsed_sec)} 경과<br>${esc(s.situation_desc)}
        <div class="btn-row" style="margin-top:8px">${recommended(s.situation, s, s.alert_id)}
        ${s.alert_id ? btn("resolve", "처리 완료", { alert: s.alert_id }) : ""}</div></div>`;
    } else {
      let msg = "예약 기록과 현장 상태가 일치합니다.";
      if (s.note && s.deadline_sec != null) {
        msg = s.note === "입실 대기" ? `입실 대기 · 체크인 마감까지 ${fmtRemain(s.deadline_sec)}`
          : `잠시 자리 비움 · ${fmtRemain(s.deadline_sec)} 뒤 '장시간 자리 비움'`;
      }
      sitHtml = `<div class="situation-box ok"><b>정상</b> · ${msg}</div>`;
    }

    let resHtml;
    if (r) {
      resHtml = `<dl class="kv">
        <dt>예약자</dt><dd>${userLine(r.user)} ${warnBadge(r.user)}</dd>
        <dt>상태</dt><dd>${r.status_label}${r.source === "admin" ? ' <span class="muted small">(관리자 배정)</span>' : ""}</dd>
        <dt>시간</dt><dd>${fmtTime(r.start_at)} ~ ${fmtTime(r.end_at)} <span class="muted small">(남은 ${fmtRemain(r.remaining_sec)})</span></dd>
        <dt>체크인</dt><dd>${r.checked_in_at ? fmtTime(r.checked_in_at) : "안 함"}</dd></dl>
        <div class="btn-row" style="margin-top:10px">
          ${r.status === "reserved" ? btn("checkin", "대리 체크인", { res: r.id, label: s.label }) : ""}
          ${btn("move", "좌석 이동", { res: r.id, label: s.label })}
          ${btn("extend", `연장 +${data.settings.extend_min}분`, { res: r.id, label: s.label })}
          ${btn("warn", "경고", { user: r.user.id, name: r.user.name })}
          ${btn("force", r.status === "reserved" ? "예약 취소" : "강제 반납", { res: r.id, label: s.label }, "danger")}
        </div>`;
    } else {
      resHtml = `<p class="muted" style="margin:0 0 8px">예약 없음</p>
        ${s.actual !== "unavailable" ? `<div class="btn-row">${btn("assign", "대리 예약·배정", { seat: s.no, checkin: s.actual === "occupied" ? 1 : 0 })}</div>` : ""}`;
    }

    el.innerHTML = `<h2>${esc(s.label)} <span class="statetag st-${s.seat_state}">${esc(s.seat_state_label)}</span>
        <span class="muted small">${esc(s.zone || "")}</span></h2>
      ${sitHtml}
      <div class="section-title">현장 상태 (임시 배분)</div>
      <div class="seg">${seg}</div>
      <p class="muted small" style="margin:6px 0 0">${SOURCE[s.actual_source] || s.actual_source} · ${fmtTime(s.actual_since)}부터 (${fmtRemain(s.actual_elapsed_sec)})
        ${s.actual_note ? ` · 사유: ${esc(s.actual_note)}` : ""}</p>
      <div class="section-title">예약 기록</div>
      ${resHtml}`;
  }

  function renderAlerts() {
    $("alert-count").textContent = alerts.length;
    if (!alerts.length) { $("alerts").innerHTML = `<li class="muted small">확인이 필요한 좌석이 없습니다.</li>`; return; }
    $("alerts").innerHTML = alerts.map((a) => {
      const s = seatByNo(a.seat_no) || { no: a.seat_no, label: a.seat_label, reservation: null };
      const ru = a.reservation && a.reservation.user;
      let actions = "";
      if (a.type === "no_show") {
        actions = ru ? btn("warn", "경고 후 확인", { user: ru.id, name: ru.name, alert: a.id, resolve: 1 }, "") : "";
      } else if (a.type === "call") {
        actions = btn("select", "좌석 보기", { seat: a.seat_no }, "");
      } else if (s.situation === a.type) {
        actions = recommended(a.type, s, a.id);
      }
      return `<li class="alert-item">
        <div class="head"><span class="seatlbl">${esc(a.seat_label)}</span>
          <span class="badge t-${a.type}">${esc(a.type_label)}</span>
          <span class="muted small">${fmtRemain(a.elapsed_sec)} 전 · ${fmtTime(a.created_at)}</span></div>
        <div class="meta">${a.desc ? esc(a.desc) + "<br>" : ""}
          ${ru ? `예약자 ${userLine(ru)} ${warnBadge(ru)}` : "예약 없음"}
          ${a.caller ? ` · 호출자 ${esc(a.caller.name)}` : ""}</div>
        ${a.memo ? `<div class="memo">${esc(a.memo)}</div>` : ""}
        <div class="btn-row">${actions}${btn("resolve", a.type === "no_show" ? "확인" : "처리 완료", { alert: a.id })}</div>
      </li>`;
    }).join("");
  }

  function renderRecon() {
    $("recon").innerHTML = data.seats.map((s) => {
      const r = s.reservation;
      const issue = s.situation !== "ok";
      return `<tr class="${issue ? "mismatch" : ""}" data-act="select" data-seat="${s.no}" style="cursor:pointer">
        <td><b>${esc(s.label)}</b></td>
        <td>${r ? `${userLine(r.user)}<br><span class="muted small">${r.status_label} · ${fmtTime(r.start_at)}~${fmtTime(r.end_at)}</span>` : '<span class="muted">예약 없음</span>'}</td>
        <td>${ACTUAL[s.actual]} <span class="muted small">(${fmtRemain(s.actual_elapsed_sec)})</span></td>
        <td><span class="statetag st-${s.seat_state}">${esc(s.seat_state_label)}</span></td>
        <td><span class="statetag ${issue ? "sit-issue" : "sit-ok"}">${issue ? "⚠ " : ""}${esc(s.situation_label)}</span>
          ${!issue && s.note ? `<span class="muted small">${esc(s.note)}</span>` : ""}</td></tr>`;
    }).join("");
  }

  function renderUsers(d) {
    $("users-note").textContent = `경고 ${d.warning_limit}회 이상이면 이용 정지를 권장합니다. 정지된 이용자는 새 예약을 할 수 없습니다.`;
    $("users").innerHTML = d.users.map((u) => {
      const r = u.reservation;
      const status = u.suspended_until
        ? `<span class="badge t-away">정지</span> <span class="muted small">~${u.suspended_until.slice(5, 10)} ${fmtTime(u.suspended_until)}</span>`
        : (u.suspend_suggested ? `<span class="badge t-return_due">정지 권장</span>` : `<span class="muted">정상</span>`);
      return `<tr>
        <td>${userLine(u)}</td>
        <td>${r ? `${esc(r.seat_label)} · ${r.status_label}` : '<span class="muted">없음</span>'}</td>
        <td><span class="warnings${u.warnings >= d.warning_limit ? " hot" : ""}">${u.warnings}</span></td>
        <td>${status}</td>
        <td><div class="btn-row">
          ${btn("warn", "경고", { user: u.id, name: u.name })}
          ${u.warnings ? btn("unwarn", "경고 취소", { user: u.id, name: u.name }) : ""}
          ${u.suspended_until ? btn("unsuspend", "정지 해제", { user: u.id, name: u.name })
            : btn("suspend", "이용 정지", { user: u.id, name: u.name }, u.suspend_suggested ? "danger" : "secondary")}
        </div></td></tr>`;
    }).join("");
  }

  function renderLog(d) {
    $("log").innerHTML = d.log.length ? d.log.map((l) => `<tr>
      <td class="small">${l.at.slice(5, 10)} ${fmtTime(l.at)}</td>
      <td><b>${esc(l.action_label)}</b></td>
      <td>${esc(l.seat_label || "")}</td>
      <td>${l.user_name ? userLine({ name: l.user_name, student_no: l.user_student_no }) : ""}</td>
      <td class="small">${esc(l.memo || "")}</td></tr>`).join("")
      : `<tr><td colspan="5" class="muted small">아직 처리 이력이 없습니다.</td></tr>`;
  }

  async function load() {
    const [s, a, u, l] = await Promise.all([
      api("GET", "/api/admin/seats", null, { quiet: true }),
      api("GET", "/api/admin/alerts?open=1", null, { quiet: true }),
      api("GET", "/api/admin/users", null, { quiet: true }),
      api("GET", "/api/admin/log?limit=30", null, { quiet: true }),
    ]);
    data = s; alerts = a.alerts; users = u.users;
    syncClock(s.server_time);
    renderChips(); renderMap(); renderDetail(); renderAlerts(); renderRecon(); renderUsers(u); renderLog(l);
    checkNewAlerts();
    $("updated").textContent = "갱신 " + s.server_time.slice(11, 19);
  }

  // ------------------------------------------------ 조치
  async function run(fn, okMsg) {
    try { const res = await fn(); if (okMsg) toast(okMsg, "ok"); return res; }
    catch (e) { return null; /* 토스트 표시됨 */ }
    finally { await ticker.refresh(); }
  }

  function userOptions() {
    return users.map((u) => {
      const why = u.suspended_until ? " — 정지 중" : (u.reservation ? ` — ${u.reservation.seat_label} ${u.reservation.status_label}` : "");
      return { value: u.id, label: `${u.name} (${u.student_no})${why}`, disabled: !!(u.suspended_until || u.reservation) };
    });
  }

  const ACTIONS = {
    select(d) {
      selected = Number(d.seat);
      renderMap(); renderDetail();
      $("detail").scrollIntoView({ behavior: "smooth", block: "start" });
    },

    async state(d) {
      const s = seatByNo(d.seat);
      if (!s || s.actual === d.state) return;
      let note = null;
      if (d.state === "unavailable") {
        note = await modal({ title: `${s.label} 사용불가 지정`,
          body: s.reservation ? `현재 ${s.reservation.user.name} 님의 예약이 있습니다. 지정 후 좌석 이동이 필요합니다.` : "사유를 적어 두면 이용자에게 안내됩니다.",
          input: { placeholder: "예: 의자 파손, 청소 중" }, ok: "사용불가로 지정" });
        if (note === false) return;
      }
      run(() => api("POST", `/api/admin/seats/${s.no}/state`, { state: d.state, note }), `${s.label} 현장 상태: ${ACTUAL[d.state]}`);
    },

    async leave(d) {
      const ok = await modal({ title: "퇴실 안내 완료", body: `${d.label} 좌석의 미예약 이용자에게 퇴실(또는 예약)을 안내했나요?\n현장 상태를 '빈자리'로 바꿉니다.`, ok: "빈자리로 변경" });
      if (ok) run(() => api("POST", `/api/admin/seats/${d.seat}/state`, { state: "empty" }), "빈자리로 변경했습니다.");
    },

    async assign(d) {
      const s = seatByNo(d.seat);
      const opts = userOptions();
      const first = opts.find((o) => !o.disabled);
      const v = await modal({
        title: `${s.label} 대리 예약·배정`,
        body: "이용자를 선택하세요. 착석한 이용자라면 '바로 이용 시작'을 고르세요.",
        fields: [
          { name: "user_id", label: "이용자", type: "select", options: opts, value: first ? first.value : "" },
          { name: "mode", label: "방식", type: "select", value: d.checkin === "1" ? "1" : "0",
            options: [{ value: "1", label: "바로 이용 시작 (현장 배정)" }, { value: "0", label: "입실 전 예약 (체크인 필요)" }] },
          { name: "memo", label: "메모 (선택)", type: "textarea", placeholder: "예: 미예약 착석자 안내 후 배정" },
        ],
        ok: "배정", okDisabled: !first,
      });
      if (!v) return;
      run(() => api("POST", "/api/admin/reservations", { user_id: Number(v.user_id), seat_no: s.no, checkin: v.mode === "1", memo: v.memo }),
        `${s.label} 좌석을 배정했습니다.`);
    },

    async checkin(d) {
      const ok = await modal({ title: "대리 체크인", body: `${d.label} 좌석에 예약자 본인이 앉아 있는지 확인했나요?`, ok: "체크인 처리" });
      if (ok) run(() => api("POST", `/api/admin/reservations/${d.res}/checkin`), "대리 체크인했습니다.");
    },

    async move(d) {
      const free = data.seats.filter((s) => s.seat_state === "available" && s.actual === "empty");
      if (!free.length) { toast("옮길 수 있는 빈자리가 없습니다.", "error"); return; }
      const v = await modal({ title: `${d.label} 예약 좌석 이동`, body: "빈자리 중에서 옮길 좌석을 고르세요.",
        fields: [
          { name: "seat_no", label: "옮길 좌석", type: "select", options: free.map((s) => ({ value: s.no, label: `${s.label} (${s.zone || ""})` })) },
          { name: "memo", label: "메모 (선택)", type: "textarea", placeholder: "예: 좌석 고장으로 이동" },
        ], ok: "이동" });
      if (!v) return;
      run(() => api("POST", `/api/admin/reservations/${d.res}/move`, { seat_no: Number(v.seat_no), memo: v.memo }), "좌석을 이동했습니다.");
    },

    async extend(d) {
      const ok = await modal({ title: "관리자 연장", body: `${d.label} 예약의 종료 시각을 ${data.settings.extend_min}분 늘립니다.\n(이용자 연장 횟수에는 포함되지 않습니다)`, ok: "연장" });
      if (ok) run(() => api("POST", `/api/admin/reservations/${d.res}/extend`), "연장했습니다.");
    },

    async force(d) {
      const memo = await modal({ title: "강제 반납", body: `${d.label} 좌석의 예약을 종료할까요?\n해당 좌석의 미해결 알림도 함께 처리됩니다.`,
        input: { placeholder: "사유 (선택) 예: 40분 이상 자리 비움" }, ok: "강제 반납", danger: true });
      if (memo !== false) run(() => api("POST", `/api/admin/reservations/${d.res}/force-return`, { memo }), "강제 반납했습니다.");
    },

    async warn(d) {
      const reason = await modal({ title: `${d.name} 님에게 경고`, body: "경고는 누적되며, 기준 횟수 이상이면 이용 정지를 권장합니다.",
        input: { placeholder: "사유 (알림에서 부여하면 비워 둬도 됩니다)" }, ok: "경고 부여", danger: true });
      if (reason === false) return;
      const body = { reason };
      if (d.alert) { body.alert_id = Number(d.alert); if (d.resolve) body.resolve = true; }
      const res = await run(() => api("POST", `/api/admin/users/${d.user}/warn`, body), `${d.name} 님에게 경고했습니다.`);
      if (res && res.suspend_suggested) {
        const days = await modal({ title: "이용 정지 권장", body: `${d.name} 님의 경고가 ${res.warnings}회 누적되었습니다. 이용을 정지할까요?`,
          fields: [{ name: "days", label: "정지 기간(일)", type: "number", min: 1, max: 90, value: data.settings.suspend_days }], ok: "이용 정지", danger: true });
        if (days) run(() => api("POST", `/api/admin/users/${d.user}/suspend`, { days: Number(days.days), reason: `경고 ${res.warnings}회 누적` }), "이용을 정지했습니다.");
      }
    },

    async unwarn(d) {
      const ok = await modal({ title: "경고 취소", body: `${d.name} 님의 경고를 1회 취소할까요?`, ok: "취소 처리" });
      if (ok) run(() => api("POST", `/api/admin/users/${d.user}/unwarn`, {}), "경고를 취소했습니다.");
    },

    async suspend(d) {
      const v = await modal({ title: `${d.name} 님 이용 정지`, body: "정지 기간 동안 새 예약을 할 수 없습니다. (현재 예약은 필요하면 강제 반납하세요)",
        fields: [
          { name: "days", label: "정지 기간(일)", type: "number", min: 1, max: 90, value: data.settings.suspend_days },
          { name: "reason", label: "사유", type: "textarea", placeholder: "예: 사석화 반복" },
        ], ok: "이용 정지", danger: true });
      if (v) run(() => api("POST", `/api/admin/users/${d.user}/suspend`, { days: Number(v.days), reason: v.reason }), "이용을 정지했습니다.");
    },

    async unsuspend(d) {
      const ok = await modal({ title: "정지 해제", body: `${d.name} 님의 이용 정지를 해제할까요?`, ok: "해제" });
      if (ok) run(() => api("POST", `/api/admin/users/${d.user}/unsuspend`), "정지를 해제했습니다.");
    },

    async resolve(d) {
      const memo = await modal({ title: "처리 완료", body: "처리 내용을 남겨 두면 처리 이력에 기록됩니다.", input: { placeholder: "예: 현장 확인 후 안내함 (선택)" }, ok: "처리 완료" });
      if (memo !== false) run(() => api("POST", `/api/admin/alerts/${d.alert}/resolve`, { memo }), "처리 완료했습니다.");
    },
  };

  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-act]");
    if (t && ACTIONS[t.dataset.act]) { e.preventDefault(); ACTIONS[t.dataset.act](t.dataset); return; }
    const seat = e.target.closest("#admin-map .seat");
    if (seat) {
      selected = Number(seat.dataset.no);
      renderMap(); renderDetail();
      if (window.innerWidth < 960) $("detail").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });

  $("btn-demo").onclick = async () => {
    const ok = await modal({ title: "시연 상황 배치",
      body: "현재 예약을 모두 취소하고 미해결 알림을 정리한 뒤,\n사용자A·B·C 등으로 다양한 예약 상황을 만듭니다.\n(정상 이용, 장시간 자리 비움, 미예약 사용, 체크인 누락, 예약 좌석 사용불가, 미입실)",
      ok: "배치", danger: true });
    if (!ok) return;
    seen = null; // 배치로 생긴 알림은 배너로 띄우지 않는다
    const res = await run(() => api("POST", "/api/admin/demo"), "시연 상황을 배치했습니다.");
    if (res) console.info(res.messages.join("\n"));
  };

  const ticker = poll(load, 3000);

  // ------------------------------------------------ 통계
  const dateEl = $("stats-date");
  dateEl.value = new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);
  async function loadStats() {
    let d;
    try { d = await api("GET", "/api/admin/stats?date=" + dateEl.value, null, { quiet: true }); } catch (e) { return; }
    const max = Math.max(0.0001, ...d.hours.map((h) => h.away_rate));
    $("chart").innerHTML = d.hours.map((h) => {
      const pct = Math.round(h.away_rate * 100);
      const title = `${h.hour}시 · 자리 비움 비율 ${pct}% · 정상 이용 ${h.in_use_min}분 · 장시간 자리 비움 ${h.away_min}분 · 미예약 사용 ${h.unauthorized_min}분`;
      return `<div class="col" title="${title}"><span class="val">${pct ? pct + "%" : ""}</span>
        <div class="bar${pct ? "" : " zero"}" style="height:${Math.max(1, (h.away_rate / max) * 85)}%"></div></div>`;
    }).join("");
    $("chart-x").innerHTML = d.hours.map((h) => `<span>${h.hour}</span>`).join("");
    const t = d.hours.reduce((a, h) => ({ u: a.u + h.in_use_min, w: a.w + h.away_min, x: a.x + h.unauthorized_min }), { u: 0, w: 0, x: 0 });
    $("stats-note").textContent = `${d.date} 합계 — 정상 이용 ${Math.round(t.u)}분 · 장시간 자리 비움 ${Math.round(t.w)}분 · 미예약 사용 ${Math.round(t.x)}분 (좌석·분 기준)`;
  }
  dateEl.onchange = loadStats;
  loadStats();
  setInterval(() => { if (document.visibilityState !== "hidden") loadStats(); }, 60000);
})();
