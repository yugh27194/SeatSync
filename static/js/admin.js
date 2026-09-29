/* /admin — 관리자 대시보드: 3상태 지도 + 예약 대조 + 상황별 조치 (3초 polling) */
(function () {
  "use strict";
  const { api, layoutGrid, poll, fmtRemain, fmtTime, syncClock, toast, modal, esc } = window.SS;
  const $ = (id) => document.getElementById(id);

  const SOURCE = { manual: "관리자 지정", camera: "카메라", checkin: "QR 체크인", return: "반납", seed: "초기 배분" };
  const MARK = { ok: "관리자 확인", issue: "관리자가 문제로 지정" };
  const NEXT = { waiting: "미입실", seated_unchecked: "미입실", away_short: "이탈", item: "사석화" };
  // 확인 필요(!) 좌석을 처리할 때 보여 주는 안내: 지금 어떤 상태인지 + 무엇을 하면 되는지
  const GUIDE = {
    unauthorized: "현장에서 이용자를 확인해 좌석을 배정하거나 퇴실을 안내하세요. 짐만 있다면 짐 주인에게 배정하거나 짐을 수거하세요.",
    no_checkin: "앉아 있는 사람이 예약자면 대리 체크인, 다른 사람이면 예약자를 다른 좌석으로 옮기세요.",
    away: "예약자가 기준 시간보다 오래 자리를 비웠습니다. 돌아오지 않으면 강제 반납하고, 반복되면 경고하세요.",
    hoarding: "짐만 두고 기준 시간보다 오래 비웠습니다. 짐을 보관 처리하고 강제 반납하거나 예약자에게 경고하세요.",
    seat_unavailable: "예약자를 다른 좌석으로 옮기거나 예약을 취소하세요.",
    away_short: "아직 기준 시간 전입니다. 곧 돌아오는지 지켜보고, 필요하면 [사전 경고]로 예약자에게 알리세요.",
    item_res: "예약자가 짐만 두고 자리를 비웠습니다. 기준 시간 전에 돌아오는지 확인하고, 필요하면 [사전 경고]를 보내세요.",
    item_nores: "예약 없이 짐만 있습니다. 짐 주인을 찾아 좌석을 배정하거나 짐을 수거하세요.",
  };
  const RES_STATUS = { reserved: "예약(입실 전)", in_use: "이용 중" };

  let data = null, alerts = [], users = [];
  let selected = Number(new URLSearchParams(location.search).get("seat")) || null; // 좌석 지도에서 누른 좌석
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
    if (fresh.length) { showBanner("확인 필요 · " + fresh.map((a) => `${a.seat_label} ${a.type_label}`).join(", ")); beep(); }
  }

  // ------------------------------------------------ 헬퍼
  const seatByNo = (no) => data && data.seats.find((s) => s.no === Number(no));
  const userLine = (u) => u ? `${esc(u.name)} <span class="muted small">(${esc(u.student_no)})</span>` : "";
  function warnBadge(u) {
    if (!u) return "";
    const hot = u.warnings >= data.settings.warning_limit;
    return `<span class="warnings${hot ? " hot" : ""}">경고 ${u.warnings}</span>` +
      (u.suspended_until ? ` <span class="badge t-suspended">정지</span>` : "");
  }
  function btn(act, label, attrs, cls) {
    const a = Object.entries(attrs || {}).map(([k, v]) => `data-${k}="${esc(v)}"`).join(" ");
    return `<button type="button" class="btn small ${cls || "secondary"}" data-act="${act}" ${a}>${label}</button>`;
  }
  const SRC_LABEL = { camera: "카메라", manual: "관리자 지정", checkin: "QR 체크인", return: "반납", seed: "초기 배분" };

  function tileSub(s) {
    const r = s.reservation;
    if (s.offer) return `🔔 ${esc(s.offer.user_name)} 안내 중`;
    if (s.check) return `<span class="warn-tag">${esc(s.detail_label)}</span>`;
    if (r && s.detail === "using") return esc(r.user.name);
    return esc(s.seat_state_label);
  }

  // 상황별 권장 조치
  function recommended(detail, s, alertId) {
    const r = s.reservation, lbl = s.label;
    switch (detail) {
      case "unauthorized":
        if (r) return btn("move", "예약자 다른 좌석으로", { res: r.id, label: lbl }, "") + btn("leave", "퇴실 안내 완료", { seat: s.no, label: lbl });
        return s.actual === "item"
          ? btn("leave", "짐 수거 완료", { seat: s.no, label: lbl, item: 1 }, "") + btn("assign", "짐 주인에게 배정", { seat: s.no, checkin: 1 })
          : btn("assign", "현장 배정", { seat: s.no, checkin: 1 }, "") + btn("leave", "퇴실 안내 완료", { seat: s.no, label: lbl });
      case "no_checkin":
        return r ? btn("checkin", "대리 체크인", { res: r.id, label: lbl }, "") +
          btn("move", "예약자 다른 좌석으로", { res: r.id, label: lbl }) : "";
      case "away":
      case "hoarding":
        return r ? btn("force", "강제 반납", { res: r.id, label: lbl }, "danger") +
          btn("warn", "예약자 경고", { user: r.user.id, name: r.user.name, alert: alertId || "" }) : "";
      case "away_short":
        return r ? btn("notice", "사전 경고", { user: r.user.id, name: r.user.name, seat: s.no, detail: s.detail }, "") : "";
      case "item":
        if (r) return btn("notice", "사전 경고", { user: r.user.id, name: r.user.name, seat: s.no, detail: s.detail }, "");
        return btn("assign", "짐 주인에게 배정", { seat: s.no, checkin: 1 }, "") + btn("leave", "짐 수거 완료", { seat: s.no, label: lbl, item: 1 });
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
      `<span class="chip st-in_use">사용중 <b>${sm.in_use}</b></span>` +
      `<span class="chip st-unavailable">사용불가 <b>${sm.unavailable}</b></span>` +
      `<span class="chip st-issue${sm.checks ? "" : " zero"}">! 확인 필요 <b>${sm.checks}</b></span>` +
      `<span class="chip plain">점유율 <b>${Math.round(data.live.occupancy * 100)}%</b> · 실사용 <b>${Math.round(data.live.actual_rate * 100)}%</b></span>` +
      `<span class="chip plain${data.waiting ? "" : " zero"}">🔔 빈자리 대기 <b>${data.waiting}</b></span>`;
  }

  function renderMap() {
    const el = $("admin-map");
    const html = [layoutGrid(el, data, 52)];
    for (const s of data.seats) {
      const check = s.check;
      html.push(`<button type="button" class="seat st-${s.seat_state}${s.booth ? " booth" : ""}${check ? " check" : ""}${selected === s.no ? " selected" : ""}"
        data-no="${s.no}" style="grid-column:${s.x};grid-row:${s.y}" title="${esc(s.zone || "")} · ${esc(s.seat_state_label)} · ${esc(s.detail_label)}">
        ${check ? '<span class="bang" aria-hidden="true">!</span>' : ""}
        ${s.stale ? '<span class="cam-off" title="카메라 감지 확인 불가 — 마지막 상태 표시 중">📷?</span>' : ""}
        ${esc(s.label)}<span class="sub">${tileSub(s)}</span></button>`);
    }
    el.innerHTML = html.join("");
  }

  // 현재 세부 상태에 해당하는 '지정' 버튼
  function currentCode(s, codes) {
    if (codes.includes(s.detail)) return s.detail;
    return { empty: "empty", occupied: "using", item: "item", unavailable: s.reason || "blocked" }[s.actual];
  }

  function assignPanel(s) {
    const codes = data.assign.groups.flatMap((g) => g.items.map((i) => i.code));
    const cur = currentCode(s, codes);
    const inUse = s.reservation && s.reservation.status === "in_use";
    return data.assign.groups.map((g) => `<div class="assign-row"><span class="assign-group st-${g.state}">${esc(g.label)}</span>
      <div class="assign-btns">${g.items.map((i) => {
        const dis = i.needs === "in_use" && !inUse;
        return `<button type="button" class="assign-btn${i.code === cur ? " on" : ""}${i.issue ? " is-issue" : ""}" data-act="state"
          data-seat="${s.no}" data-detail="${i.code}" data-label="${esc(i.label)}" data-group="${g.state}"${dis ? ' disabled title="이용 중인 예약이 있는 좌석만"' : ""}>
          ${i.issue ? "! " : ""}${esc(i.label)}</button>`;
      }).join("")}</div></div>`).join("");
  }

  function renderDetail() {
    const el = $("detail");
    const s = seatByNo(selected);
    if (!s) { el.innerHTML = `<h2>좌석 상세 · 조치</h2><p class="empty">좌석을 선택하세요.</p>`; return; }
    const r = s.reservation;

    let sitHtml;
    if (s.check) {
      let when = `${fmtRemain(s.elapsed_sec)}째`;
      if (s.deadline_sec != null && NEXT[s.detail]) when += ` · ${fmtRemain(s.deadline_sec)} 뒤 '${NEXT[s.detail]}'`;
      const guide = s.detail === "item" ? GUIDE[r ? "item_res" : "item_nores"] : GUIDE[s.detail];
      sitHtml = `<div class="situation-box i-${s.detail}"><b>! ${esc(s.seat_state_label)} · ${esc(s.detail_label)}</b> · 확인 필요 · ${when}
        <p class="sit-desc">${esc(s.detail_desc)}</p>
        ${guide ? `<p class="sit-guide"><b>처리 방법</b> ${esc(guide)}</p>` : ""}
        <div class="btn-row" style="margin-top:8px">${recommended(s.detail, s, s.alert_id)}
        ${s.alert_id ? btn("resolve", "처리 완료", { alert: s.alert_id }) : ""}</div></div>`;
    } else {
      let msg = s.detail_desc || "";
      if (s.deadline_sec != null && NEXT[s.detail]) msg = `${fmtRemain(s.deadline_sec)} 뒤 '${NEXT[s.detail]}'`;
      sitHtml = `<div class="situation-box ok"><b>${esc(s.seat_state_label)} · ${esc(s.detail_label)}</b>${msg ? " — " + esc(msg) : ""}</div>`;
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
          ${btn("notice", "사전 경고", { user: r.user.id, name: r.user.name, seat: s.no, detail: s.detail })}
          ${btn("warn", "경고(누적)", { user: r.user.id, name: r.user.name })}
          ${btn("force", r.status === "reserved" ? "예약 취소" : "강제 반납", { res: r.id, label: s.label }, "danger")}
        </div>`;
    } else {
      resHtml = `<p class="muted" style="margin:0 0 8px">예약 없음</p>
        ${s.actual !== "unavailable" ? `<div class="btn-row">${btn("assign", "대리 예약·배정", { seat: s.no, checkin: s.actual === "occupied" ? 1 : 0 })}</div>` : ""}`;
    }

    el.innerHTML = `<h2>${esc(s.label)} <span class="statetag st-${s.seat_state}">${esc(s.seat_state_label)}</span>
        <span class="muted small">${esc(s.zone || "")}</span></h2>
      ${sitHtml}
      <div class="section-title">판정 확인</div>
      <div class="feedback-row">
        <span class="small">화면 판정 <b>${esc(s.seat_state_label)} · ${esc(s.detail_label)}</b>
          <span class="muted">(${SRC_LABEL[s.actual_source] || s.actual_source})</span> — 실제와 같나요?</span>
        <div class="btn-row">${btn("fb_ok", "✓ 맞음", { seat: s.no }, "ok-btn")}${btn("fb_wrong", "✗ 틀림", { seat: s.no }, "danger")}</div>
      </div>
      ${s.camera ? `<p class="small cam-line" style="margin:6px 0 0">📷 ${esc(s.camera.camera_id)}·${esc(s.camera.camera_seat)}
        · ${s.camera.state ? { OCCUPIED: "사람 있음", EMPTY: "사람 없음", UNKNOWN: "확인 불가" }[s.camera.state] : "수신 없음"}
        ${s.camera.confidence ? ` (점수 ${s.camera.confidence.toFixed(2)})` : ""}
        ${s.camera.seen_at ? ` · ${fmtTime(s.camera.seen_at)} 수신` : ""}
        ${s.stale ? ' · <b class="warn-text">감지 끊김 — 마지막 상태 유지, 이탈·사석화 판정 보류</b>' : ""}</p>` : ""}
      ${s.offer ? `<p class="small" style="margin:6px 0 0">🔔 빈자리 알림 대기자 <b>${esc(s.offer.user_name)}</b> 님에게 안내 중 (${fmtRemain(s.offer.left_sec)} 남음)</p>` : ""}
      <div class="section-title">좌석 상태 지정 (임시 배분)</div>
      ${assignPanel(s)}
      <p class="muted small" style="margin:6px 0 0">현장: ${esc(s.actual_label)}${s.mark ? ` · ${MARK[s.mark]}` : ""} · ${SOURCE[s.actual_source] || s.actual_source}
        · ${fmtTime(s.actual_since)}부터 (${fmtRemain(s.actual_elapsed_sec)})${s.note ? ` · 메모: ${esc(s.note)}` : ""}</p>
      <details class="hint"><summary>세부 상태 안내</summary>
        <p><b>이용 중</b>: 관리자가 확인한 정상 사용입니다. 예약이 없어도 무단 점유로 바뀌지 않습니다.<br>
        <b>짐만 있음</b>: 지도에 붉게 !(확인 필요)로 표시되지만 경고·알림은 보내지 않습니다.
        (이용 중 예약이 있는 좌석이면 사석화 기준 시간이 지난 뒤 사석화로 바뀝니다)<br>
        <b>! 무단 점유 · 이탈 · 사석화</b>: 기준 시간을 기다리지 않고 바로 조치 목록에 올립니다. 이탈·사석화는 이용 중인 예약이 있는 좌석만 지정할 수 있습니다.<br>
        <b>카메라가 연결되면</b> 좌석마다 사람·짐·비어 있음이 자동으로 갱신됩니다(사용불가 지정 좌석 제외).</p></details>
      <div class="section-title">예약 기록</div>
      ${resHtml}`;
  }

  function renderAlerts() {
    $("alert-count").textContent = alerts.length;
    if (!alerts.length) { $("alerts").innerHTML = `<li class="muted small">조치할 좌석이 없습니다.</li>`; return; }
    $("alerts").innerHTML = alerts.map((a) => {
      const s = seatByNo(a.seat_no) || { no: a.seat_no, label: a.seat_label, reservation: null };
      const ru = a.reservation && a.reservation.user;
      let actions = "";
      if (a.type === "no_show") {
        actions = ru ? btn("warn", "경고 후 확인", { user: ru.id, name: ru.name, alert: a.id, resolve: 1 }, "") : "";
      } else if (a.type === "call") {
        actions = btn("select", "좌석 보기", { seat: a.seat_no }, "");
      } else if (s.detail === a.type) {
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
      const issue = s.needs_action;
      return `<tr class="${issue ? `mismatch i-${s.detail}` : ""}" data-act="select" data-seat="${s.no}" style="cursor:pointer">
        <td><b>${esc(s.label)}</b><br><span class="muted small">${esc(s.zone || "")}</span></td>
        <td>${r ? `${userLine(r.user)}<br><span class="muted small">${r.status_label} · ${fmtTime(r.start_at)}~${fmtTime(r.end_at)}</span>` : '<span class="muted">예약 없음</span>'}</td>
        <td>${esc(s.actual_label)} <span class="muted small">(${fmtRemain(s.actual_elapsed_sec)})</span></td>
        <td><span class="statetag st-${s.seat_state}">${esc(s.seat_state_label)}</span></td>
        <td>${issue ? `<span class="badge t-${s.detail}">! ${esc(s.detail_label)}</span>` : esc(s.detail_label)}</td></tr>`;
    }).join("");
  }

  function renderUsers(d) {
    $("users-note").textContent = `경고 ${d.warning_limit}회 이상이면 이용 정지를 권장합니다. 정지된 이용자는 새 예약을 할 수 없습니다.`;
    $("users").innerHTML = d.users.map((u) => {
      const r = u.reservation;
      const status = u.suspended_until
        ? `<span class="badge t-suspended">정지</span> <span class="muted small">~${u.suspended_until.slice(5, 10)} ${fmtTime(u.suspended_until)}</span>`
        : (u.suspend_suggested ? `<span class="badge t-return_due">정지 권장</span>` : `<span class="muted">정상</span>`);
      return `<tr>
        <td>${userLine(u)}</td>
        <td>${r ? `${esc(r.seat_label)} · ${r.status_label}` : '<span class="muted">없음</span>'}</td>
        <td><span class="warnings${u.warnings >= d.warning_limit ? " hot" : ""}">${u.warnings}</span></td>
        <td>${status}</td>
        <td><div class="btn-row">
          ${btn("notice", "사전 경고", { user: u.id, name: u.name })}
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
      <td>${esc(l.admin_name || "")}</td>
      <td>${esc(l.seat_label || "")}</td>
      <td>${l.user_name ? userLine({ name: l.user_name, student_no: l.user_student_no }) : ""}</td>
      <td class="small">${esc(l.memo || "")}</td></tr>`).join("")
      : `<tr><td colspan="6" class="muted small">아직 처리 이력이 없습니다.</td></tr>`;
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
      if (!s) return;
      let note = null;
      if (d.group === "unavailable") {
        note = await modal({ title: `${s.label} 사용불가 · ${d.label}`,
          body: s.reservation ? `현재 ${s.reservation.user.name} 님의 예약이 있습니다. 지정 후 좌석 이동이 필요합니다.` : "메모를 적어 두면 이용자에게도 안내됩니다. (선택)",
          input: { placeholder: "예: 의자 파손, 청소 중" }, ok: "지정" });
        if (note === false) return;
      }
      run(() => api("POST", `/api/admin/seats/${s.no}/state`, { detail: d.detail, note }), `${s.label} → ${d.label}`);
    },

    async leave(d) {
      const ok = d.item
        ? await modal({ title: "짐 수거 완료", body: `${d.label} 좌석에 방치된 짐을 수거(보관)했나요?\n현장을 '비어 있음'으로 바꿉니다.`, ok: "비어 있음으로 변경" })
        : await modal({ title: "퇴실 안내 완료", body: `${d.label} 좌석의 무단 점유자에게 퇴실(또는 예약)을 안내했나요?\n현장을 '비어 있음'으로 바꿉니다.`, ok: "비어 있음으로 변경" });
      if (ok) run(() => api("POST", `/api/admin/seats/${d.seat}/state`, { detail: "empty" }), "현장을 비어 있음으로 바꿨습니다.");
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
          { name: "memo", label: "메모 (선택)", type: "textarea", placeholder: "예: 무단 점유자 안내 후 배정" },
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
          { name: "seat_no", label: "옮길 좌석", type: "select", options: free.map((s) => ({ value: s.no, label: `${s.label} · ${s.zone || ""}` })) },
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

    async fb_ok(d) {
      const s = seatByNo(d.seat);
      run(() => api("POST", `/api/admin/seats/${d.seat}/feedback`, { verdict: "correct" }),
        `${s.label} 판정 '맞음'을 기록했습니다.`).then(loadAccuracy);
    },

    async fb_wrong(d) {
      const s = seatByNo(d.seat);
      const inUse = s.reservation && s.reservation.status === "in_use";
      const options = data.assign.groups.flatMap((g) => g.items.map((i) => ({
        value: i.code, label: `${g.label} · ${i.label}`, disabled: i.needs === "in_use" && !inUse })));
      const v = await modal({ title: `${s.label} 판정이 틀렸어요`,
        body: `화면 판정: ${s.seat_state_label} · ${s.detail_label} (${SRC_LABEL[s.actual_source] || s.actual_source})\n실제 좌석 상태를 골라 주세요.`,
        fields: [
          { name: "correct", label: "실제 상태", type: "select", options, value: options.find((o) => !o.disabled && o.value !== s.detail)?.value },
          { name: "apply", label: "좌석에 바로 반영", type: "select", value: "1",
            options: [{ value: "1", label: "예 — 이 상태로 수정" }, { value: "0", label: "아니오 — 기록만" }] },
          { name: "memo", label: "메모 (선택)", type: "textarea", placeholder: "예: 가방을 사람으로 인식함" },
        ], ok: "피드백 기록", danger: true });
      if (!v) return;
      run(() => api("POST", `/api/admin/seats/${d.seat}/feedback`,
        { verdict: "wrong", correct_detail: v.correct, apply: v.apply === "1", memo: v.memo }),
        "판정 피드백을 기록했습니다.").then(loadAccuracy);
    },

    async notice(d) {
      const PRESET = {
        away_short: "자리를 오래 비우고 계세요. 곧 이탈로 처리될 수 있으니 돌아오시거나 반납해 주세요.",
        item: "짐만 두고 자리를 비우셨어요. 곧 사석화로 처리될 수 있으니 돌아오시거나 반납해 주세요.",
        away: "이탈 상태입니다. 바로 돌아오시지 않으면 반납 처리됩니다.",
        hoarding: "사석화 상태입니다. 바로 돌아오시지 않으면 반납 처리됩니다.",
        no_checkin: "좌석에 계시다면 좌석 QR로 체크인해 주세요.",
        waiting: "체크인 마감 전에 좌석 QR로 체크인해 주세요.",
      };
      const msg = await modal({ title: `${d.name} 님에게 사전 경고`,
        body: "본인 계정으로 주의 알림만 보냅니다. 누적 경고 횟수에는 포함되지 않아요.",
        input: { value: PRESET[d.detail] || "좌석 이용 규정을 지켜 주세요. 계속되면 경고가 부여될 수 있어요." },
        ok: "보내기" });
      if (msg === false) return;
      run(() => api("POST", `/api/admin/users/${d.user}/notice`, { message: msg, seat_no: d.seat ? Number(d.seat) : null }),
        "사전 경고를 보냈습니다.");
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
      body: "현재 예약을 모두 취소하고 미해결 알림을 정리한 뒤,\n사용자A·B·C·테스트 계정으로 좌석에 다양한 상황을 만듭니다.\n(config/seats.json의 demo 목록: 정상 이용, 이탈, 무단 점유, 짐만 있음, 사석화, 고장 등)",
      ok: "배치", danger: true });
    if (!ok) return;
    seen = null; // 배치로 생긴 알림은 배너로 띄우지 않는다
    const res = await run(() => api("POST", "/api/admin/demo"), "시연 상황을 배치했습니다.");
    if (res) console.info(res.messages.join("\n"));
  };

  $("btn-sample").onclick = async () => {
    const ok = await modal({ title: "샘플 이력 생성",
      body: "지난 4주 동안의 예약·이용·상태 기록을 샘플로 만듭니다.\n'내 기록'과 '혼잡도' 화면을 시연할 때 쓰세요. (실제 데이터와 섞이니 시연용 DB에서만 사용)",
      ok: "생성" });
    if (!ok) return;
    const res = await run(() => api("POST", "/api/admin/demo-history", { weeks: 4 }));
    if (res) toast(`샘플 이력 생성 완료 · 예약 ${res.reservations}건`, "ok");
  };

  // ------------------------------------------------ 판정 정확도
  async function loadAccuracy() {
    let d;
    try { d = await api("GET", "/api/admin/feedback", null, { quiet: true }); } catch (e) { return; }
    const pctv = (a) => (a.accuracy == null ? "-" : Math.round(a.accuracy * 100) + "%");
    if (!d.overall.total) {
      $("accuracy").innerHTML = `<p class="muted small">아직 피드백이 없습니다. 좌석을 누르고 [✓ 맞음] / [✗ 틀림]을 기록해 보세요.</p>`;
      return;
    }
    const src = Object.entries(d.by_source).map(([k, a]) => `<span class="chip plain">${SRC_LABEL[k] || k} <b>${pctv(a)}</b> <span class="muted">(${a.correct}/${a.total})</span></span>`).join("");
    const st = Object.entries(d.by_state).map(([k, a]) => `<span class="chip plain">${esc(k)} <b>${pctv(a)}</b></span>`).join("");
    const conf = d.confusion.map((c) => `<li>${esc(c.shown)} → 실제 ${esc(c.correct)} <b>${c.count}회</b></li>`).join("");
    const recent = d.recent.map((f) => `<tr><td class="small">${f.at.slice(5, 10)} ${fmtTime(f.at)}</td><td>${esc(f.seat_label)}</td>
      <td class="small">${esc(f.shown)} <span class="muted">(${SRC_LABEL[f.source] || f.source})</span></td>
      <td>${f.verdict === "correct" ? '<span class="pill green">맞음</span>' : `<span class="pill red">틀림</span> ${esc(f.correct || "")}${f.applied ? ' <span class="muted small">수정함</span>' : ""}`}</td>
      <td class="small">${esc(f.admin_name || "")}${f.memo ? " · " + esc(f.memo) : ""}</td></tr>`).join("");
    $("accuracy").innerHTML = `<div class="acc-top"><div class="acc-big">${pctv(d.overall)}</div>
        <div><b>전체 정확도</b> · ${d.overall.correct}/${d.overall.total}건 맞음<div class="chips" style="margin:6px 0 0">${src}</div>
        <div class="chips" style="margin:4px 0 0">${st}</div></div></div>
      ${conf ? `<div class="section-title">자주 틀리는 판정</div><ul class="conf">${conf}</ul>` : ""}
      <div class="section-title">최근 피드백</div>
      <div class="table-wrap"><table class="recon"><thead><tr><th>시각</th><th>좌석</th><th>화면 판정</th><th>결과</th><th>확인자·메모</th></tr></thead>
      <tbody>${recent}</tbody></table></div>`;
  }
  loadAccuracy();
  setInterval(() => { if (document.visibilityState !== "hidden") loadAccuracy(); }, 30000);

  // ------------------------------------------------ 카메라 연결 상태
  const CAM_STATE = { OCCUPIED: "사람", EMPTY: "없음", UNKNOWN: "확인 불가" };
  async function loadCameras() {
    let d;
    try { d = await api("GET", "/api/admin/cameras", null, { quiet: true }); } catch (e) { return; }
    $("cameras").innerHTML = d.cameras.map((c) => {
      const status = !c.connected ? `<span class="pill">연결 기록 없음</span>`
        : c.fresh ? `<span class="pill green">정상</span>` : `<span class="pill red">끊김 · ${esc(c.health || "")}</span>`;
      return `<div class="cam-card"><div class="ri-head"><b>📷 ${esc(c.camera_id)}</b> ${status}
          <span class="muted small">${c.last_seen_at ? `마지막 수신 ${fmtRemain(c.last_seen_sec)} 전` : "아직 수신 없음"}
          ${c.meaning ? ` · 감지 범위: ${c.meaning === "person_presence_only" ? "사람만(짐 구분 없음)" : esc(c.meaning)}` : ""}
          ${c.clock_offset ? ` · 시계 보정 ${c.clock_offset}초` : ""}</span></div>
        <div class="cam-seats">${c.seats.map((st) => `<span class="cam-seat cs-${esc(st.cam_state || "NONE")}"
          title="${esc(st.camera_seat)} → ${esc(st.label)}${st.confidence ? " · 점수 " + st.confidence.toFixed(2) : ""}">
          ${esc(st.camera_seat)}→<b>${esc(st.label)}</b> ${st.cam_state ? CAM_STATE[st.cam_state] : "-"}</span>`).join("")}</div></div>`;
    }).join("") || `<p class="muted small">카메라가 연결된 좌석이 없습니다. config/seats.json에 camera_id·camera_seat를 지정하세요.</p>`;
  }
  loadCameras();
  setInterval(() => { if (document.visibilityState !== "hidden") loadCameras(); }, 5000);

  const ticker = poll(load, 3000);

  // ------------------------------------------------ 통계
  const dateEl = $("stats-date");
  dateEl.value = new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);
  async function loadStats() {
    let d;
    try { d = await api("GET", "/api/admin/stats?date=" + dateEl.value, null, { quiet: true }); } catch (e) { return; }
    const max = Math.max(0.0001, ...d.hours.map((h) => h.issue_rate));
    $("chart").innerHTML = d.hours.map((h) => {
      const pct = Math.round(h.issue_rate * 100);
      const title = `${h.hour}시 · 이탈·사석화 비율 ${pct}% · 정상 이용 ${h.in_use_min}분 · 이탈 ${h.away_min}분 · 사석화 ${h.hoarding_min}분 · 무단 점유 ${h.unauthorized_min}분`;
      return `<div class="col" title="${title}"><span class="val">${pct ? pct + "%" : ""}</span>
        <div class="bar${pct ? "" : " zero"}" style="height:${Math.max(1, (h.issue_rate / max) * 85)}%"></div></div>`;
    }).join("");
    $("chart-x").innerHTML = d.hours.map((h) => `<span>${h.hour}</span>`).join("");
    const t = d.hours.reduce((a, h) => ({ u: a.u + h.in_use_min, w: a.w + h.away_min, o: a.o + h.hoarding_min, x: a.x + h.unauthorized_min }), { u: 0, w: 0, o: 0, x: 0 });
    $("stats-note").textContent = `${d.date} 합계 — 정상 이용 ${Math.round(t.u)}분 · 이탈 ${Math.round(t.w)}분 · 사석화 ${Math.round(t.o)}분 · 무단 점유 ${Math.round(t.x)}분 (좌석·분 기준)`;
  }
  dateEl.onchange = loadStats;
  loadStats();
  setInterval(() => { if (document.visibilityState !== "hidden") loadStats(); }, 60000);
})();
