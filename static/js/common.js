/* SeatSync 공통 헬퍼: api(), poll(), fmtRemain(), toast(), modal() */
(function () {
  "use strict";

  function toast(msg, kind, ms) {
    const box = document.getElementById("toasts");
    if (!box) return alert(msg);
    const el = document.createElement("div");
    el.className = "toast" + (kind ? " " + kind : "");
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(() => el.remove(), ms || 3200);
  }

  class ApiError extends Error {
    constructor(status, code, message) { super(message); this.status = status; this.code = code; }
  }

  /** JSON API 호출. 에러 JSON이면 토스트를 띄우고 ApiError를 던진다. opts.quiet=true면 토스트 생략. */
  async function api(method, url, body, opts) {
    opts = opts || {};
    try {
      return await apiOnce(method, url, body, opts);
    } catch (e) {
      // 관리자 권한이 필요한 요청이면 코드 입력을 받아 해금한 뒤 한 번 다시 시도한다
      if (e.code === "ADMIN_REQUIRED" && !opts._retried && (await requireAdmin(null, true))) {
        return apiOnce(method, url, body, { ...opts, _retried: true });
      }
      throw e;
    }
  }

  function csrfToken() {
    const m = document.querySelector('meta[name="csrf-token"]');
    if (m && m.content) return m.content;
    const c = document.cookie.split("; ").find((x) => x.startsWith("csrftoken="));
    return c ? decodeURIComponent(c.slice(10)) : "";
  }

  let adminPrompt = null;
  /** "관리자 권한이 필요합니다." → 관리자 코드 입력 → 해금. 성공하면 true. 동시에 여러 번 불려도 창은 하나. */
  function requireAdmin(reason, force, stay) {
    if (!force && document.body.dataset.admin === "1") return Promise.resolve(true);
    if (adminPrompt) return adminPrompt;
    adminPrompt = (async () => {
      let msg = reason || "관리자 코드를 입력하세요.\n로그인한 동안은 다시 묻지 않아요.";
      for (;;) {
        const v = await modal({ title: "🔒 관리자 탭", body: msg, ok: "관리자 탭 열기",
          fields: [{ name: "code", label: "관리자 코드", type: "password" }] });
        if (!v) {
          if (!stay && location.pathname.startsWith("/admin")) location.href = "/map";
          return false;
        }
        try {
          await apiOnce("POST", "/api/admin-mode/unlock", { code: v.code }, { quiet: true });
          document.body.dataset.admin = "1";
          return true;
        } catch (e) {
          if (e.code === "ADMIN_LOCKED") { toast(e.message, "error", 5000); return false; }
          msg = e.message;
        }
      }
    })();
    adminPrompt.finally(() => { adminPrompt = null; });
    return adminPrompt;
  }

  async function apiOnce(method, url, body, opts) {
    const init = { method: method, headers: { "Accept": "application/json" }, credentials: "same-origin" };
    if (method !== "GET") init.headers["X-CSRFToken"] = csrfToken();
    if (body !== undefined && body !== null) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(url, init);
    } catch (e) {
      if (!opts.quiet) toast("서버에 연결할 수 없어요. 잠시 후 다시 시도해 주세요.", "error");
      throw new ApiError(0, "NETWORK", "서버에 연결할 수 없어요.");
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* 본문 없음 */ }
    if (!res.ok) {
      if (res.status === 401) {
        location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
      }
      const err = (data && data.error) || { code: "HTTP_" + res.status, message: "요청을 처리하지 못했어요. 다시 시도해 주세요." };
      if (!opts.quiet && err.code !== "ADMIN_REQUIRED") toast(err.message, "error");
      throw new ApiError(res.status, err.code, err.message);
    }
    return data;
  }

  /** ms마다 fn 실행. 탭이 숨겨지면 멈추고, 다시 보이면 즉시 1회 실행 후 재개. */
  function poll(fn, ms) {
    let timer = null, running = null, again = false;
    // 이미 조회 중이면 끝난 뒤 한 번 더 조회한다(조치 직후 새로고침이 이전 응답에 묻히지 않게)
    function tick() {
      if (running) { again = true; return running; }
      running = (async () => {
        do {
          again = false;
          try { await fn(); } catch (e) { /* 다음 주기에 재시도 */ }
        } while (again);
        running = null;
      })();
      return running;
    }
    function start() { if (!timer) { tick(); timer = setInterval(tick, ms); } }
    function stop() { if (timer) { clearInterval(timer); timer = null; } }
    document.addEventListener("visibilitychange", () => {
      document.visibilityState === "hidden" ? stop() : start();
    });
    if (document.visibilityState !== "hidden") start();
    return { refresh: tick, stop: stop, start: start };
  }

  /** 초 → "1시간 12분" */
  function fmtRemain(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
    if (h > 0) return h + "시간 " + m + "분";
    if (m > 0) return m + "분";
    return sec + "초";
  }

  /** 초 → "01:12:05" (카운트다운용) */
  function fmtClock(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const p = (n) => String(n).padStart(2, "0");
    return (h > 0 ? h + ":" : "") + p(m) + ":" + p(s);
  }

  /** ISO 시각 → "14:03" (KST 표시는 서버가 +09:00로 내려줌) */
  function fmtTime(iso) {
    if (!iso) return "-";
    const m = /T(\d{2}):(\d{2})/.exec(iso);
    return m ? m[1] + ":" + m[2] : iso;
  }

  function parseTs(iso) { return iso ? Date.parse(iso) / 1000 : null; }

  /** 서버-클라이언트 시계 차이 보정 */
  let clockSkew = 0;
  function syncClock(serverIso) { const t = parseTs(serverIso); if (t) clockSkew = t - Date.now() / 1000; }
  function serverNow() { return Date.now() / 1000 + clockSkew; }

  /**
   * 확인 모달. opts: {title, body, ok, cancel, danger, input:{placeholder,value}, fields:[...]}
   *  - input만 있으면 → Promise<false | string>
   *  - fields가 있으면 → Promise<false | {name: value}>
   *    field: {name, label, type:'select'|'textarea'|'number', options:[{value,label,disabled}], value, placeholder, min, max}
   *  - 둘 다 없으면 → Promise<boolean>
   */
  function modal(opts) {
    return new Promise((resolve) => {
      const back = document.createElement("div");
      back.className = "modal-back";
      const m = document.createElement("div");
      m.className = "modal" + (opts.alert ? " modal-alert" : "");
      m.setAttribute("role", opts.alert ? "alertdialog" : "dialog");
      const h = document.createElement("h3"); h.textContent = opts.title || "확인";
      m.append(h);
      if (opts.body) { const b = document.createElement("div"); b.className = "body"; b.textContent = opts.body; m.append(b); }
      const fields = opts.fields || (opts.input ? [{ name: "_", type: "textarea", ...opts.input }] : []);
      const els = {};
      const ok = document.createElement("button");
      for (const f of fields) {
        if (f.label) { const l = document.createElement("label"); l.textContent = f.label; m.append(l); }
        let el;
        if (f.type === "select") {
          el = document.createElement("select");
          for (const o of f.options || []) {
            const op = document.createElement("option");
            op.value = o.value; op.textContent = o.label; op.disabled = !!o.disabled;
            el.append(op);
          }
          if (f.value != null) el.value = f.value;
        } else if (f.type === "password") {
          el = document.createElement("input"); el.type = "password"; el.autocomplete = "off"; el.setAttribute("autocapitalize", "off");
          if (f.inputmode) el.inputMode = f.inputmode;
          el.addEventListener("keydown", (e) => { if (e.key === "Enter") ok.click(); });
        } else if (f.type === "number") {
          el = document.createElement("input"); el.type = "number"; el.inputMode = "numeric";
          if (f.min != null) el.min = f.min; if (f.max != null) el.max = f.max;
          el.value = f.value != null ? f.value : "";
        } else {
          el = document.createElement("textarea"); el.maxLength = 200; el.value = f.value || "";
        }
        el.placeholder = f.placeholder || "";
        el.style.marginBottom = "10px";
        els[f.name] = el;
        m.append(el);
      }
      const row = document.createElement("div"); row.className = "btn-row"; row.style.marginTop = "6px";
      const no = document.createElement("button"); no.className = "btn secondary"; no.textContent = opts.cancel || "취소";
      ok.className = "btn" + (opts.danger ? " danger" : ""); ok.textContent = opts.ok || "확인";
      if (opts.okDisabled) ok.disabled = true;
      if (opts.noCancel) row.append(ok); else row.append(no, ok);
      m.append(row); back.append(m);
      document.body.append(back);
      (Object.values(els)[0] || ok).focus();
      function close(v) { back.remove(); document.removeEventListener("keydown", onKey); resolve(v); }
      function onKey(e) { if (e.key === "Escape") close(false); }
      document.addEventListener("keydown", onKey);
      no.onclick = () => close(false);
      back.onclick = (e) => { if (e.target === back) close(false); };
      ok.onclick = () => {
        if (!fields.length) return close(true);
        const vals = {};
        for (const [k, el] of Object.entries(els)) vals[k] = el.value.trim();
        close(opts.fields ? vals : vals._);
      };
    });
  }

  /** 좌석 지도 그리드: 좌석만 그린다. 좌석이 없는 열·행(구역 사이 간격)은 좁게. 구조물(fixtures)이 있으면 함께 그린다. */
  function layoutGrid(el, data, colMin) {
    const g = data.grid;
    const rows = [];
    for (let r = 1; r <= g.rows; r++) {
      if (data.seats.some((s) => s.y === r)) { rows.push("minmax(60px, auto)"); continue; }
      const kinds = data.fixtures.filter((f) => f.y <= r && r < f.y + (f.h || 1)).map((f) => f.kind || "etc");
      if (kinds.includes("window")) rows.push("22px");
      else if (kinds.some((k) => k !== "aisle")) rows.push(kinds.includes("table") ? "30px" : "40px");
      else rows.push("12px");
    }
    const cols = [];
    for (let c = 1; c <= g.cols; c++) {
      cols.push(data.seats.some((s) => s.x === c) || data.fixtures.some((f) => f.x <= c && c < f.x + (f.w || 1))
        ? `minmax(${colMin || 56}px, 1fr)` : "24px");
    }
    el.style.gridTemplateColumns = cols.join(" ");
    el.style.gridTemplateRows = rows.join(" ");
    return data.fixtures.map((f) => `<div class="fixture fx-${esc(f.kind || "etc")}"
      style="grid-column:${f.x} / span ${f.w || 1};grid-row:${f.y} / span ${f.h || 1}">${esc(f.label)}</div>`).join("");
  }

  /** [관리자] 탭: 이 세션에서 처음이면 관리자 코드를 묻고, 맞으면 관리자 탭으로 들어간다. 한 번 통과하면 다시 묻지 않는다. */
  function bindAdminTab() {
    const tab = document.getElementById("admin-tab");
    if (!tab) return;
    tab.addEventListener("click", async (e) => {
      if (document.body.dataset.admin === "1") return;  // 이미 통과한 세션 → 그대로 이동
      e.preventDefault();
      if (await requireAdmin(null, false, true)) location.href = "/admin";
    });
  }
  document.addEventListener("DOMContentLoaded", bindAdminTab);

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // ------------------------------------------------ 본인 알림 (사전 경고·빈자리 안내 등)
  const BANNER_KINDS = new Set(["prewarn", "issue", "warning", "suspend", "offer"]);
  let lastBannerKey = "", toasted = new Set(), notices = [];

  async function markRead(ids) {
    try { await apiOnce("POST", "/api/me/notifications/read", ids ? { ids } : {}, { quiet: true }); } catch (e) { /* 무시 */ }
  }

  function renderBanners(list) {
    const stack = document.getElementById("notice-stack");
    if (!stack) return;
    const key = list.map((n) => n.id).join(",");
    if (key === lastBannerKey) return;
    lastBannerKey = key;
    stack.innerHTML = list.map((n) => `<div class="notice-banner lv-${esc(n.level)}" data-id="${n.id}">
      <div class="nb-text"><b>${esc(n.title)}</b>${n.body ? `<span>${esc(n.body)}</span>` : ""}</div>
      <div class="nb-btns">
        ${n.kind === "offer" && n.level === "ok" && location.pathname !== "/map" ? `<a class="btn small" href="/map">좌석 지도</a>` : ""}
        <button type="button" class="btn small secondary" data-nb-ok="${n.id}">확인</button>
      </div></div>`).join("");
  }

  async function pollNotices() {
    if (!document.getElementById("bell")) return;
    let d;
    try { d = await apiOnce("GET", "/api/me/notifications", null, { quiet: true }); } catch (e) { return; }
    notices = d.items;
    const cnt = document.getElementById("bell-count");
    cnt.textContent = d.unread > 9 ? "9+" : d.unread;
    cnt.classList.toggle("hidden", !d.unread);
    const unread = d.items.filter((n) => !n.read);
    renderBanners(unread.filter((n) => BANNER_KINDS.has(n.kind)).slice(0, 3));
    const infos = unread.filter((n) => !BANNER_KINDS.has(n.kind) && !toasted.has(n.id));
    infos.forEach((n) => { toasted.add(n.id); toast(`${n.title}${n.body ? " · " + n.body : ""}`, n.level === "ok" ? "ok" : "", 5000); });
    if (infos.length) markRead(infos.map((n) => n.id));
  }

  function openNoticeList() {
    const back = document.createElement("div");
    back.className = "modal-back";
    const items = notices.length ? notices.map((n) => `<li class="nl-item lv-${esc(n.level)}${n.read ? "" : " unread"}">
        <div class="nl-title">${esc(n.title)}</div>${n.body ? `<div class="nl-body">${esc(n.body)}</div>` : ""}
        <div class="nl-time">${n.created_at.slice(5, 10).replace("-", "/")} ${fmtTime(n.created_at)}</div></li>`).join("")
      : `<li class="muted small">알림이 없어요.</li>`;
    back.innerHTML = `<div class="modal notice-list" role="dialog"><h3>🔔 알림</h3><ul>${items}</ul>
      <div class="btn-row"><button type="button" class="btn secondary" data-close>닫기</button></div></div>`;
    document.body.append(back);
    back.addEventListener("click", (e) => { if (e.target === back || e.target.closest("[data-close]")) back.remove(); });
    markRead(null).then(() => { lastBannerKey = ""; pollNotices(); });
  }

  document.addEventListener("click", (e) => {
    const ok = e.target.closest("[data-nb-ok]");
    if (ok) {
      const id = Number(ok.dataset.nbOk);
      ok.closest(".notice-banner").remove();
      markRead([id]).then(pollNotices);
      return;
    }
    if (e.target.closest("#bell")) openNoticeList();
  });
  document.addEventListener("DOMContentLoaded", () => { if (document.getElementById("bell")) poll(pollNotices, 10000); });

  /** 분(15초 단위 소수) → "15분", "1분 30초", "45초" */
  function fmtMin(v) {
    const total = Math.round(Number(v) * 60), m = Math.floor(total / 60), s = total % 60;
    return m && s ? `${m}분 ${s}초` : m ? `${m}분` : `${s}초`;
  }

  /** 예약자 → 관리자: "내 예약 좌석에 다른 사람이 앉아 있어요" (관리자 화면에 팝업으로 뜬다) */
  async function reportSeatTaken(seatNo, seatLabel) {
    const memo = await modal({ title: "내 자리에 다른 사람이 앉아 있어요",
      body: `${seatLabel} 좌석 상황을 관리자에게 바로 알려요.`,
      ok: "관리자에게 알리기", input: { placeholder: "덧붙일 말 (선택)" } });
    if (memo === false) return;
    try {
      const res = await api("POST", "/api/calls", { seat_no: seatNo, memo, kind: "seat_taken" });
      toast(res.duplicate ? "방금 알렸어요. 잠시만 기다려 주세요." : "관리자에게 알렸어요. 곧 도와드릴게요.", "ok", 5000);
    } catch (e) { /* 토스트 표시됨 */ }
  }

  // ------------------------------------------------ 관리자: 이용자 호출 팝업
  // 관리자 코드를 통과한 세션이면 어느 화면에 있든 새 호출(특히 "내 예약 좌석에 다른 사람이 앉아 있음")을 팝업으로 띄운다.
  const ACK_KEY = "ss_ack_calls";
  function ackedCalls() {
    try { return new Set(JSON.parse(sessionStorage.getItem(ACK_KEY) || "[]")); } catch (e) { return new Set(); }
  }
  function ackCalls(ids) {
    const s = ackedCalls(); ids.forEach((i) => s.add(i));
    try { sessionStorage.setItem(ACK_KEY, JSON.stringify([...s].slice(-200))); } catch (e) { /* 무시 */ }
  }
  function beep() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = 880; g.gain.value = 0.08; o.connect(g); g.connect(ctx.destination);
      o.start(); o.stop(ctx.currentTime + 0.25);
    } catch (e) { /* 소리 없이 팝업만 */ }
  }
  let callPopupOpen = false;
  async function pollCalls() {
    if (document.body.dataset.admin !== "1" || callPopupOpen) return;
    let d;
    try { d = await apiOnce("GET", "/api/admin/alerts?open=1", null, { quiet: true }); } catch (e) { return; }
    const acked = ackedCalls();
    const fresh = d.alerts.filter((a) => a.type === "call" && !acked.has(a.id));
    if (!fresh.length) return;
    callPopupOpen = true;
    beep();
    const lines = fresh.map((a) => {
      const who = a.caller ? `${a.caller.name}(${a.caller.student_no})` : "이용자";
      const res = a.reservation ? ` · 예약자 ${a.reservation.user.name}` : "";
      return `[${a.seat_label}] ${a.call_label || "관리자 호출"}\n  보낸 사람 ${who}${res} · ${fmtTime(a.created_at)}` +
        (a.memo ? `\n  "${a.memo}"` : "");
    });
    const first = fresh[0];
    const go = await modal({ alert: true, title: `📣 이용자 호출 ${fresh.length}건`, body: lines.join("\n\n"),
      ok: `${first.seat_label} 처리하기`, cancel: "나중에" });
    ackCalls(fresh.map((a) => a.id));
    callPopupOpen = false;
    if (go) location.href = `/admin?seat=${first.seat_no}`;
  }
  document.addEventListener("DOMContentLoaded", () => { if (document.body.dataset.admin === "1") poll(pollCalls, 5000); });

  window.SS = { fmtMin, api, requireAdmin, reportSeatTaken, layoutGrid, poll, refreshNotices: pollNotices, fmtRemain, fmtClock, fmtTime, parseTs, syncClock, serverNow, toast, modal, esc, ApiError };
  window.api = api; window.poll = poll; window.fmtRemain = fmtRemain;
})();
