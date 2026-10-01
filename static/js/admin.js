/* /admin — 관리자 대시보드: 좌석 지도(좌석명만) + 좌석 상세·조치 + 조치 목록 + 예약 대조·카메라 (3초 polling) */
(function () {
  "use strict";
  const { api, layoutGrid, poll, fmtRemain, fmtTime, syncClock, toast, modal, esc } = window.SS;
  const $ = (id) => document.getElementById(id);

  const SOURCE = { manual: "관리자 지정", camera: "카메라", checkin: "QR 체크인", return: "반납", seed: "초기 배분" };
  const MARK = { ok: "관리자 확인", issue: "관리자가 문제로 지정" };
  // 확인 필요(!) 좌석을 처리할 때 보여 주는 안내: 지금 어떤 상태인지 + 무엇을 하면 되는지
  const GUIDE = {
    unauthorized: "현장을 확인해 좌석을 배정하거나 퇴실을 안내하세요. 짐만 있으면 수거하세요.",
    no_checkin: "예약자 본인이면 대리 체크인, 다른 사람이면 퇴실을 안내하세요.",
    away: "돌아오지 않으면 강제 반납하고, 반복되면 경고하세요.",
    hoarding: "짐을 보관하고 강제 반납하거나 경고하세요.",
    seat_unavailable: "예약자를 다른 좌석으로 옮기거나 예약을 취소하세요.",
    no_show: "오지 않으면 예약을 취소하고, 늦게 왔다면 대리 체크인하세요.",
    away_short: "곧 돌아오는지 지켜보세요. 필요하면 [사전 경고]를 보내세요.",
    unknown: "현장을 보고 실제 상태를 지정하세요. 여러 좌석이 함께 판단 불가면 카메라를 점검하세요.",
    item_res: "곧 돌아오는지 지켜보세요. 필요하면 [사전 경고]를 보내세요.",
    item_nores: "짐 주인에게 좌석을 배정하거나 짐을 수거하세요.",
  };
  const RES_STATUS = { reserved: "예약(입실 전)", in_use: "이용 중" };
  // 조치 영역 아래 붙는 사유 문장: 세부 상태 설명 + 짐 여부(짐 감지 ON일 때)
  const ITEM_DETAILS = ["item", "hoarding"];
  function reason(desc, s) {
    let t = desc || "";
    if (s && s.has_item && !ITEM_DETAILS.includes(s.detail)) t += (t ? " " : "") + "좌석에 짐이 있어요.";
    return t;
  }

  let data = null, alerts = [], users = [];
  let selected = Number(new URLSearchParams(location.search).get("seat")) || null; // 좌석 지도에서 누른 좌석
  let seen = null;

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
    const fresh = alerts.filter((a) => !seen.has(a.id) && a.type !== "call");  // 호출은 공통 팝업(common.js)으로 알린다
    fresh.forEach((a) => seen.add(a.id));
    if (fresh.length) { showBanner("확인 필요 · " + fresh.map((a) => `${a.seat_label} ${a.type_label}`).join(", ")); }
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

  // 이 좌석이 아닌 다른 좌석의 예약(잘못 앉은 예약자 후보)
  const otherReservations = (s) => data.seats.filter((x) => x.no !== s.no && x.reservation);

  // 상황별 권장 조치
  function recommended(detail, s, alertId) {
    const r = s.reservation, lbl = s.label;
    switch (detail) {
      case "unauthorized":
        if (r) return btn("move", "예약자 다른 좌석으로", { res: r.id, label: lbl }, "") + btn("leave", "퇴실 안내 완료", { seat: s.no, label: lbl });
        if (s.actual === "occupied" && otherReservations(s).length)  // 다른 좌석 예약자가 여기 잘못 앉았을 수 있다
          return btn("pull", "다른 좌석 예약자가 앉음 → 이 좌석으로", { seat: s.no }, "") +
            btn("assign", "현장 배정", { seat: s.no, checkin: 1 }) + btn("leave", "퇴실 안내 완료", { seat: s.no, label: lbl });
        return s.actual === "item"
          ? btn("leave", "짐 수거 완료", { seat: s.no, label: lbl, item: 1 }, "") + btn("assign", "짐 주인에게 배정", { seat: s.no, checkin: 1 })
          : btn("assign", "현장 배정", { seat: s.no, checkin: 1 }, "") + btn("leave", "퇴실 안내 완료", { seat: s.no, label: lbl });
      case "no_show":
        return r ? btn("force", "예약 취소(미입실)", { res: r.id, label: lbl, noshow: 1 }, "danger") +
          btn("checkin", "대리 체크인", { res: r.id, label: lbl }) : "";
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
      case "unknown": // 현장 확인 결과를 관리자가 지정 → 카메라가 회복될 때까지 그 상태로 판정
        return btn("state", "현장 확인: 사람 있음", { seat: s.no, detail: "using", label: "사람 있음(확인)" }, "") +
          btn("state", "현장 확인: 짐만 있음", { seat: s.no, detail: "item", label: "짐만 있음(확인)" }) +
          btn("state", "현장 확인: 비어 있음", { seat: s.no, detail: "empty", label: "비어 있음(확인)" });
      case "seat_unavailable":
        return r ? btn("move", "다른 좌석으로 이동", { res: r.id, label: lbl }, "") +
          btn("force", "예약 취소", { res: r.id, label: lbl }, "danger") : "";
      default:
        return "";
    }
  }

  // ------------------------------------------------ 렌더링
  function renderChips() {
    const c = data.categories, sm = data.summary;
    // 항목 수·순서가 고정된 타일 — 값이 바뀌어도 자리가 움직이지 않는다
    const tile = (cls, label, n, sub) => `<div class="stat-tile t-${cls}${n ? "" : " zero"}">
      <div class="st-num">${n}</div><div class="st-label">${label}</div><div class="st-sub">${sub || "&nbsp;"}</div></div>`;
    $("chips").innerHTML =
      tile("normal", "정상", c.normal, "사람이 이용 중") +
      tile("away", "이석", c.away, c.away_long ? `확인 필요 ${c.away_long}` : "자리 비움") +
      tile("unauthorized", "무단 점유", c.unauthorized, "체크인 없이 감지") +
      tile("unknown", "판단 불가", c.unknown, "카메라 인식 오류") +
      tile("issue", "! 관리자 확인", sm.issues, "조치 목록");
    $("live-line").textContent = `점유율 ${Math.round(data.live.occupancy * 100)}% · 실사용 ${Math.round(data.live.actual_rate * 100)}%` +
      ` · 빈자리 ${c.empty}석 · 빈자리 대기 ${data.waiting}명` + (c.unavailable ? ` · 사용불가 ${c.unavailable}석` : "");
  }

  function renderMap() {
    const el = $("admin-map");
    const lg = $("lg-item");
    if (lg) lg.hidden = !data.item_detection;
    const html = [layoutGrid(el, data, 52)];
    for (const s of data.seats) {
      const bang = s.needs_action;  // ! = 관리자 확인 필요
      html.push(`<button type="button" class="seat cat-${s.category}${s.booth ? " booth" : ""}${bang ? " check" : ""}${selected === s.no ? " selected" : ""}"
        data-no="${s.no}" style="grid-column:${s.x};grid-row:${s.y}" title="${esc(s.label)} · ${esc(s.category_label)} · ${esc(s.detail_label)}" aria-label="${esc(s.label)} ${esc(s.category_label)}${s.has_item ? " · 짐 있음" : ""}${bang ? " · 관리자 확인 필요" : ""}">
        ${bang ? '<span class="bang" aria-hidden="true">!</span>' : ""}
        ${s.has_item ? '<span class="item-dot" title="짐 있음"></span>' : ""}
        ${s.stale && s.category !== "unknown" ? '<span class="cam-off" title="카메라 끊김 · 마지막 상태 표시 중">📷?</span>' : ""}
        <span class="sl">${esc(s.label)}</span><span class="sub">${esc(s.category_label)}</span></button>`);
    }
    el.innerHTML = html.join("");
  }

  // 좌석 상태 지정: 빈자리 / 예약 / 사용중 / 사용불가 (예약은 사용중과 같은 주황색)
  function assignPanel(s) {
    const r = s.reservation;
    const cur = s.seat_state === "available" ? "empty" : s.seat_state === "unavailable" ? "unavailable"
      : (r && r.status === "reserved" ? "reserve" : "using");
    const b = (code, label, cls, attrs, dis) => `<button type="button" class="assign-btn a-${cls}${code === cur ? " on" : ""}" ${attrs}${dis ? ` disabled title="${dis}"` : ""}>${label}</button>`;
    return `<div class="assign-btns">
      ${b("empty", "빈자리", "available", `data-act="state" data-seat="${s.no}" data-detail="empty" data-label="빈자리"`)}
      ${b("reserve", "예약", "in_use", `data-act="reserve" data-seat="${s.no}"`,
          r ? "이미 예약이 있는 좌석" : s.seat_state === "unavailable" ? "사용불가 좌석" : "")}
      ${b("using", "사용중", "in_use", `data-act="state" data-seat="${s.no}" data-detail="using" data-label="사용중"`)}
      ${b("unavailable", "사용불가", "unavailable", `data-act="state" data-seat="${s.no}" data-detail="blocked" data-label="사용불가" data-group="unavailable"`)}
    </div>`;
  }

  function renderDetail() {
    const el = $("detail");
    const s = seatByNo(selected);
    if (!s) { el.innerHTML = `<h2>좌석 상세 · 조치</h2><p class="empty">좌석을 선택하세요.</p>`; return; }
    const r = s.reservation;

    let sitHtml;
    if (s.check || s.category !== "normal") {
      let when = `${fmtRemain(s.elapsed_sec)}째`;
      if (s.deadline_sec != null && s.next_label) when += ` · ${fmtRemain(s.deadline_sec)} 뒤 '${s.next_label}'`;
      const guide = s.detail === "item" ? GUIDE[r ? "item_res" : "item_nores"] : GUIDE[s.detail];
      sitHtml = `<div class="situation-box i-${s.detail}"><b>${s.needs_action ? "! " : ""}${esc(s.category_label)}</b>${s.needs_action ? " · 관리자 확인 필요" : ""} · ${when}
        <p class="sit-desc">${esc(reason(s.detail_desc, s))}</p>
        ${guide ? `<p class="sit-guide"><b>처리 방법</b> ${esc(guide)}</p>` : ""}
        <div class="btn-row" style="margin-top:8px">${recommended(s.detail, s, s.alert_id)}
        ${s.alert_id ? btn("resolve", "처리 완료", { alert: s.alert_id }) : ""}</div></div>`;
    } else {
      let msg = reason(s.detail_desc, s);
      if (s.deadline_sec != null && s.next_label) msg += ` ${fmtRemain(s.deadline_sec)} 뒤 '${s.next_label}'.`;
      sitHtml = `<div class="situation-box ok"><b>${esc(s.category_label)}</b>${msg ? " — " + esc(msg) : ""}</div>`;
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
          ${btn("extend", `연장 +${SS.fmtMin(data.settings.extend_min)}`, { res: r.id, label: s.label })}
          ${btn("notice", "사전 경고", { user: r.user.id, name: r.user.name, seat: s.no, detail: s.detail })}
          ${btn("warn", "경고(누적)", { user: r.user.id, name: r.user.name })}
          ${btn("force", r.status === "reserved" ? "예약 취소" : "강제 반납", { res: r.id, label: s.label }, "danger")}
        </div>`;
    } else {
      resHtml = `<p class="muted" style="margin:0 0 8px">예약 없음</p>
        ${s.actual !== "unavailable" ? `<div class="btn-row">${btn("assign", "대리 예약·배정", { seat: s.no, checkin: s.actual === "occupied" ? 1 : 0 })}
          ${s.actual === "occupied" && otherReservations(s).length ? btn("pull", "다른 좌석 예약자가 앉음", { seat: s.no }) : ""}</div>` : ""}`;
    }

    el.innerHTML = `<h2>${esc(s.label)} <span class="statetag cat-${s.category}">${esc(s.category_label)}</span>
        <span class="muted small">${esc(s.zone || "")}</span></h2>
      ${sitHtml}
      ${s.camera ? `<p class="small cam-line" style="margin:6px 0 0">📷 ${esc(s.camera.camera_id)}·${esc(s.camera.camera_seat)}
        · ${s.camera.state ? { OCCUPIED: "사람 있음", EMPTY: "사람 없음", UNKNOWN: "확인 불가" }[s.camera.state] : "수신 없음"}
        ${s.camera.confidence ? ` (점수 ${s.camera.confidence.toFixed(2)})` : ""}
        ${s.camera.seen_at ? ` · ${fmtTime(s.camera.seen_at)} 수신` : ""}
        ${s.stale ? ` · <b class="warn-text">${s.detail === "unknown" ? "판단 불가 · 현장 확인 필요" : "카메라 끊김 · 마지막 상태 표시 중"}</b>` : ""}</p>` : ""}
      ${s.offer ? `<p class="small" style="margin:6px 0 0">🔔 빈자리 알림 대기자 <b>${esc(s.offer.user_name)}</b> 님에게 안내 중 (${fmtRemain(s.offer.left_sec)} 남음)</p>` : ""}
      <div class="section-title">좌석 상태 지정</div>
      ${assignPanel(s)}
      <p class="muted small" style="margin:6px 0 0">현장: ${esc(s.actual_label)}${s.mark ? ` · ${MARK[s.mark]}` : ""} · ${SOURCE[s.actual_source] || s.actual_source}
        · ${fmtTime(s.actual_since)}부터 (${fmtRemain(s.actual_elapsed_sec)})${s.note ? ` · 메모: ${esc(s.note)}` : ""}</p>
      <div class="section-title">예약 기록</div>
      ${resHtml}`;
  }

  function renderAlerts() {
    $("alert-count").textContent = alerts.length;
    if (!alerts.length) { $("alerts").innerHTML = `<li class="muted small">조치할 좌석이 없어요.</li>`; return; }
    $("alerts").innerHTML = alerts.map((a) => {
      const s = seatByNo(a.seat_no) || { no: a.seat_no, label: a.seat_label, reservation: null };
      const ru = a.reservation && a.reservation.user;
      let actions = "";
      if (a.type === "call" && a.call_kind === "seat_taken" && s.reservation) {
        // 예약자 신고: 예약 좌석에 다른 사람이 앉아 있음
        const r = s.reservation;
        actions = btn("leave", "착석자 퇴실 안내 완료", { seat: s.no, label: s.label, keep: 1, alert: a.id }, "") +
          btn("move", "예약자 다른 좌석으로", { res: r.id, label: s.label }) +
          btn("select", "좌석 보기", { seat: a.seat_no });
      } else if (a.type === "call") {
        actions = btn("select", "좌석 보기", { seat: a.seat_no }, "");
      } else if (s.detail === a.type) {
        actions = recommended(a.type, s, a.id);
      }
      return `<li class="alert-item">
        <div class="head"><span class="seatlbl">${esc(a.seat_label)}</span>
          <span class="badge t-${a.type}">${esc(a.type_label)}</span>
          <span class="muted small">${fmtRemain(a.elapsed_sec)} 전 · ${fmtTime(a.created_at)}</span></div>
        <div class="meta">${a.desc ? esc(a.type === "call" ? a.desc : reason(a.desc, s.detail === a.type ? s : null)) + "<br>" : ""}
          ${ru ? `예약자 ${userLine(ru)} ${warnBadge(ru)}` : "예약 없음"}
          ${a.caller ? ` · 호출자 ${esc(a.caller.name)}` : ""}</div>
        ${a.call_label && a.call_kind ? `<div class="memo"><b>${esc(a.call_label)}</b></div>` : ""}
        ${a.memo ? `<div class="memo">${esc(a.memo)}</div>` : ""}
        <div class="btn-row">${actions}${btn("resolve", "처리 완료", { alert: a.id })}</div>
      </li>`;
    }).join("");
  }

  const CAM_STATE = { OCCUPIED: "사람 있음", EMPTY: "비어 있음", UNKNOWN: "확인 불가" };
  function camCell(s) {
    const c = s.camera;
    if (!c) return '<span class="muted small">카메라 없음</span>';
    const st = c.state ? CAM_STATE[c.state] : "수신 없음";
    const ago = c.seen_at ? ` · ${fmtRemain(Math.max(0, (Date.parse(data.server_time) - Date.parse(c.seen_at)) / 1000))} 전` : "";
    const cls = !c.state ? "cs-NONE" : (c.fresh ? `cs-${c.state}` : "cs-UNKNOWN");
    const item = data.item_detection && c.fresh && c.item !== null && c.item !== undefined
      ? ` · 짐 ${c.item ? "있음" : "없음"}` : "";
    return `<span class="cam-seat ${cls}">${esc(c.camera_seat)} ${st}${item}</span><span class="muted small">${ago}${!c.fresh && c.state ? " · 끊김" : ""}</span>`;
  }

  function renderRecon() {
    $("recon").innerHTML = data.seats.map((s) => {
      const r = s.reservation;
      return `<tr class="${s.needs_action ? "mismatch" : ""}" data-act="select" data-seat="${s.no}" style="cursor:pointer">
        <td><b>${esc(s.label)}</b></td>
        <td>${r ? `${userLine(r.user)}<br><span class="muted small">${r.status === "reserved" ? "예약(체크인 전)" : "QR 체크인"} · ${fmtTime(r.start_at)}~${fmtTime(r.end_at)}</span>` : '<span class="muted">없음</span>'}</td>
        <td class="hide-sm">${camCell(s)}</td>
        <td class="hide-sm">${esc(s.actual_label)}</td>
        <td><span class="statetag cat-${s.category}">${esc(s.category_label)}</span>${s.needs_action ? ' <b class="warn-text">!</b>' : ""}
          <div class="small muted">${esc(reason(s.detail_desc, s))}</div></td></tr>`;
    }).join("");
  }

  function renderLog(d) {
    $("log").innerHTML = d.log.length ? d.log.map((l) => `<tr>
      <td class="small">${l.at.slice(5, 10)} ${fmtTime(l.at)}</td>
      <td><b>${esc(l.action_label)}</b></td>
      <td class="hide-sm">${esc(l.admin_name || (l.action === "auto_return" ? "자동" : ""))}</td>
      <td>${esc(l.seat_label || "")}</td>
      <td class="hide-sm">${l.user_name ? userLine({ name: l.user_name, student_no: l.user_student_no }) : ""}</td>
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
    renderChips(); renderMap(); renderDetail(); renderAlerts(); renderRecon(); renderLog(l);
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
        note = await modal({ title: `${s.label} 사용불가로 지정`,
          body: s.reservation ? `현재 ${s.reservation.user.name} 님의 예약이 있습니다. 지정 후 좌석 이동이 필요합니다.` : "메모를 적어 두면 이용자에게도 안내됩니다. (선택)",
          input: { placeholder: "예: 의자 파손, 청소 중" }, ok: "지정" });
        if (note === false) return;
      }
      run(() => api("POST", `/api/admin/seats/${s.no}/state`, { detail: d.detail, note }), `${s.label} → ${d.label}`);
    },

    async leave(d) {
      if (d.keep) {  // 예약자 신고(내 자리에 다른 사람) 처리: 착석자를 내보내고 예약자가 앉을 수 있게 비움
        const ok = await modal({ title: "착석자 퇴실 안내 완료", body: `${d.label} 좌석을 비우고 신고를 처리할까요?\n예약자에게 QR 체크인을 안내해 주세요.`, ok: "처리" });
        if (ok) run(async () => {
          await api("POST", `/api/admin/seats/${d.seat}/state`, { detail: "empty" });
          if (d.alert) await api("POST", `/api/admin/alerts/${d.alert}/resolve`, { memo: "착석자 퇴실 안내 → 예약자 좌석 확보" });
        }, "착석자를 내보내고 신고를 처리했습니다.");
        return;
      }
      const ok = d.item
        ? await modal({ title: "짐 수거 완료", body: `${d.label} 좌석을 비어 있음으로 바꿀까요?`, ok: "비어 있음으로 변경" })
        : await modal({ title: "퇴실 안내 완료", body: `${d.label} 좌석을 비어 있음으로 바꿀까요?`, ok: "비어 있음으로 변경" });
      if (ok) run(() => api("POST", `/api/admin/seats/${d.seat}/state`, { detail: "empty" }), "현장을 비어 있음으로 바꿨습니다.");
    },

    async assign(d) {
      const s = seatByNo(d.seat);
      const opts = userOptions();
      const first = opts.find((o) => !o.disabled);
      const v = await modal({
        title: `${s.label} 대리 예약·배정`,
        body: "앉아 있는 사람이면 '바로 이용 시작'을 고르세요.",
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

    async reserve(d) {
      const s = seatByNo(d.seat);
      const opts = userOptions();
      const first = opts.find((o) => !o.disabled);
      const v = await modal({
        title: `${s.label} 예약`,
        body: `${SS.fmtMin(data.settings.checkin_limit_min)} 안에 체크인하지 않으면 미입실로 표시돼요.`,
        fields: [
          { name: "user_id", label: "이용자", type: "select", options: opts, value: first ? first.value : "" },
          { name: "memo", label: "메모 (선택)", type: "textarea", placeholder: "예: 전화 예약" },
        ],
        ok: "예약", okDisabled: !first,
      });
      if (!v) return;
      run(() => api("POST", "/api/admin/reservations", { user_id: Number(v.user_id), seat_no: s.no, checkin: false, memo: v.memo }),
        `${s.label} 좌석을 예약했습니다.`);
    },

    // 예약자가 다른 좌석에 잘못 앉음 → 그 예약을 지금 앉은 좌석으로 옮기고 바로 체크인
    async pull(d) {
      const s = seatByNo(d.seat);
      const opts = otherReservations(s).map((x) => ({ value: x.reservation.id,
        label: `${x.reservation.user.name} — ${x.label} ${x.reservation.status === "reserved" ? "예약(체크인 전)" : "이용 중"}` }));
      const v = await modal({ title: `${s.label}에 앉은 사람이 다른 좌석 예약자인가요?`,
        body: `본인 확인 후 예약을 ${s.label}(으)로 옮기고 바로 체크인해요.`,
        fields: [{ name: "res", label: "예약", type: "select", options: opts, value: opts[0].value },
          { name: "memo", label: "메모 (선택)", type: "textarea", placeholder: "예: 좌석 착각" }], ok: "옮기고 체크인" });
      if (!v) return;
      run(() => api("POST", `/api/admin/reservations/${v.res}/move`, { seat_no: s.no, seated: true, memo: v.memo || "좌석 착각" }),
        `예약을 ${s.label}(으)로 옮기고 체크인했습니다.`);
    },

    async checkin(d) {
      const ok = await modal({ title: "대리 체크인", body: `${d.label} 좌석에 예약자 본인이 앉아 있나요?`, ok: "체크인 처리" });
      if (ok) run(() => api("POST", `/api/admin/reservations/${d.res}/checkin`), "대리 체크인했습니다.");
    },

    async move(d) {
      const free = data.seats.filter((s) => s.seat_state === "available" && s.actual === "empty");
      if (!free.length) { toast("옮길 수 있는 빈자리가 없습니다.", "error"); return; }
      const v = await modal({ title: `${d.label} 예약 좌석 이동`, body: "옮길 빈자리를 고르세요.",
        fields: [
          { name: "seat_no", label: "옮길 좌석", type: "select", options: free.map((s) => ({ value: s.no, label: `${s.label} · ${s.zone || ""}` })) },
          { name: "memo", label: "메모 (선택)", type: "textarea", placeholder: "예: 좌석 고장으로 이동" },
        ], ok: "이동" });
      if (!v) return;
      run(() => api("POST", `/api/admin/reservations/${d.res}/move`, { seat_no: Number(v.seat_no), memo: v.memo }), "좌석을 이동했습니다.");
    },

    async extend(d) {
      const ok = await modal({ title: "관리자 연장", body: `${d.label} 예약을 ${SS.fmtMin(data.settings.extend_min)} 늘려요. (연장 횟수에는 포함되지 않아요)`, ok: "연장" });
      if (ok) run(() => api("POST", `/api/admin/reservations/${d.res}/extend`), "연장했습니다.");
    },

    async force(d) {
      if (d.noshow) {
        const m = await modal({ title: "미입실 예약 취소", body: `${d.label} 예약을 미입실로 취소할까요?`,
          input: { placeholder: "메모 (선택)" }, ok: "예약 취소", danger: true });
        if (m !== false) run(() => api("POST", `/api/admin/reservations/${d.res}/force-return`, { memo: m, no_show: true }), "미입실로 예약을 취소했습니다.");
        return;
      }
      const memo = await modal({ title: "강제 반납", body: `${d.label} 예약을 종료할까요?`,
        input: { placeholder: "사유 (선택) 예: 40분 이상 자리 비움" }, ok: "강제 반납", danger: true });
      if (memo !== false) run(() => api("POST", `/api/admin/reservations/${d.res}/force-return`, { memo }), "강제 반납했습니다.");
    },

    async warn(d) {
      const reason = await modal({ title: `${d.name} 님에게 경고`, body: "경고는 누적돼요.",
        input: { placeholder: "사유 (알림에서 부여하면 비워 둬도 됩니다)" }, ok: "경고 부여", danger: true });
      if (reason === false) return;
      const body = { reason };
      if (d.alert) { body.alert_id = Number(d.alert); if (d.resolve) body.resolve = true; }
      const res = await run(() => api("POST", `/api/admin/users/${d.user}/warn`, body), `${d.name} 님에게 경고했습니다.`);
      if (res && res.suspend_suggested) {
        const days = await modal({ title: "이용 정지 권장", body: `경고가 ${res.warnings}회 쌓였어요. 이용을 정지할까요?`,
          fields: [{ name: "days", label: "정지 기간(일)", type: "number", min: 1, max: 90, value: data.settings.suspend_days }], ok: "이용 정지", danger: true });
        if (days) run(() => api("POST", `/api/admin/users/${d.user}/suspend`, { days: Number(days.days), reason: `경고 ${res.warnings}회 누적` }), "이용을 정지했습니다.");
      }
    },

    async unwarn(d) {
      const ok = await modal({ title: "경고 취소", body: `${d.name} 님의 경고를 1회 취소할까요?`, ok: "취소 처리" });
      if (ok) run(() => api("POST", `/api/admin/users/${d.user}/unwarn`, {}), "경고를 취소했습니다.");
    },

    async suspend(d) {
      const v = await modal({ title: `${d.name} 님 이용 정지`, body: "정지 기간에는 새 예약을 할 수 없어요.",
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

    async notice(d) {
      const PRESET = {
        away_short: "자리를 오래 비우고 계세요. 돌아오시거나 반납해 주세요. 계속 비우면 반납될 수 있어요.",
        item: "짐만 두고 자리를 비우셨어요. 돌아오시거나 반납해 주세요. 계속 비우면 반납될 수 있어요.",
        away: "자리를 오래 비우셨어요. 바로 돌아오시지 않으면 반납 처리됩니다.",
        hoarding: "짐만 두고 오래 자리를 비우셨어요. 바로 돌아오시지 않으면 반납 처리됩니다.",
        no_checkin: "좌석에 계시다면 좌석 QR로 체크인해 주세요.",
        waiting: "체크인 마감 전에 좌석 QR로 체크인해 주세요.",
      };
      const msg = await modal({ title: `${d.name} 님에게 사전 경고`,
        body: "주의 알림만 보내요. 경고 횟수에는 포함되지 않아요.",
        input: { value: PRESET[d.detail] || "좌석 이용 규정을 지켜 주세요. 계속되면 경고가 부여될 수 있어요." },
        ok: "보내기" });
      if (msg === false) return;
      run(() => api("POST", `/api/admin/users/${d.user}/notice`, { message: msg, seat_no: d.seat ? Number(d.seat) : null }),
        "사전 경고를 보냈습니다.");
    },

    async resolve(d) {
      const memo = await modal({ title: "처리 완료", input: { placeholder: "메모 (선택)" }, ok: "처리 완료" });
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
      $("detail").scrollIntoView({ behavior: "smooth", block: "start" });  // 상세는 지도 바로 아래 카드
    }
  });

  // ------------------------------------------------ 카메라 연결 상태
  async function loadCameras() {
    let d;
    try { d = await api("GET", "/api/admin/cameras", null, { quiet: true }); } catch (e) { return; }
    $("cam-line").innerHTML = d.cameras.map((c) => {
      const status = !c.connected ? `<span class="pill">연결 기록 없음</span>`
        : c.fresh ? `<span class="pill green">정상</span>` : `<span class="pill red">끊김${c.health ? " · " + esc(c.health) : ""}</span>`;
      return `<span class="cam-chip">📷 <b>${esc(c.camera_id)}</b> ${status}
        <span class="muted small">${c.last_seen_at ? `마지막 수신 ${fmtRemain(c.last_seen_sec)} 전` : "아직 수신 없음"} · 좌석 ${c.seats.length}개</span></span>`;
    }).join("") || `<span class="muted small">카메라가 연결된 좌석이 없습니다.</span>`;
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
      const title = `${h.hour}시 · 장기 이석·사석화 비율 ${pct}% · 정상 이용 ${h.in_use_min}분 · 장기 이석 ${h.away_min}분 · 사석화 ${h.hoarding_min}분 · 무단 점유 ${h.unauthorized_min}분`;
      return `<div class="col" title="${title}"><span class="val">${pct ? pct + "%" : ""}</span>
        <div class="bar${pct ? "" : " zero"}" style="height:${Math.max(1, (h.issue_rate / max) * 85)}%"></div></div>`;
    }).join("");
    $("chart-x").innerHTML = d.hours.map((h) => `<span>${h.hour}</span>`).join("");
    const t = d.hours.reduce((a, h) => ({ u: a.u + h.in_use_min, w: a.w + h.away_min, o: a.o + h.hoarding_min, x: a.x + h.unauthorized_min }), { u: 0, w: 0, o: 0, x: 0 });
    $("stats-note").textContent = `${d.date} 합계 — 정상 이용 ${Math.round(t.u)}분 · 장기 이석 ${Math.round(t.w)}분 · 사석화 ${Math.round(t.o)}분 · 무단 점유 ${Math.round(t.x)}분 (좌석·분 기준)`;
  }
  dateEl.onchange = loadStats;
  loadStats();
  setInterval(() => { if (document.visibilityState !== "hidden") loadStats(); }, 60000);
})();
