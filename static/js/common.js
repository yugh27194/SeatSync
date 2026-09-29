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
    const init = { method: method, headers: { "Accept": "application/json" }, credentials: "same-origin" };
    if (body !== undefined && body !== null) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(url, init);
    } catch (e) {
      if (!opts.quiet) toast("서버에 연결할 수 없습니다.", "error");
      throw new ApiError(0, "NETWORK", "서버에 연결할 수 없습니다.");
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* 본문 없음 */ }
    if (!res.ok) {
      if (res.status === 401) {
        location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
      }
      const err = (data && data.error) || { code: "HTTP_" + res.status, message: "요청을 처리하지 못했습니다." };
      if (!opts.quiet) toast(err.message, "error");
      throw new ApiError(res.status, err.code, err.message);
    }
    return data;
  }

  /** ms마다 fn 실행. 탭이 숨겨지면 멈추고, 다시 보이면 즉시 1회 실행 후 재개. */
  function poll(fn, ms) {
    let timer = null, running = false;
    async function tick() {
      if (running) return;
      running = true;
      try { await fn(); } catch (e) { /* 다음 주기에 재시도 */ }
      finally { running = false; }
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

  /** 확인 모달. opts: {title, body, ok, cancel, danger, input:{placeholder,value}} → Promise<false|true|string> */
  function modal(opts) {
    return new Promise((resolve) => {
      const back = document.createElement("div");
      back.className = "modal-back";
      const m = document.createElement("div");
      m.className = "modal";
      m.setAttribute("role", "dialog");
      const h = document.createElement("h3"); h.textContent = opts.title || "확인";
      const b = document.createElement("div"); b.className = "body"; b.textContent = opts.body || "";
      m.append(h, b);
      let input = null;
      if (opts.input) {
        input = document.createElement("textarea");
        input.placeholder = opts.input.placeholder || "";
        input.value = opts.input.value || "";
        input.maxLength = 200;
        input.style.marginBottom = "12px";
        m.append(input);
      }
      const row = document.createElement("div"); row.className = "btn-row";
      const no = document.createElement("button"); no.className = "btn secondary"; no.textContent = opts.cancel || "취소";
      const ok = document.createElement("button"); ok.className = "btn" + (opts.danger ? " danger" : ""); ok.textContent = opts.ok || "확인";
      row.append(no, ok); m.append(row); back.append(m);
      document.body.append(back);
      (input || ok).focus();
      function close(v) { back.remove(); document.removeEventListener("keydown", onKey); resolve(v); }
      function onKey(e) { if (e.key === "Escape") close(false); }
      document.addEventListener("keydown", onKey);
      no.onclick = () => close(false);
      back.onclick = (e) => { if (e.target === back) close(false); };
      ok.onclick = () => close(input ? input.value.trim() : true);
    });
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  window.SS = { api, poll, fmtRemain, fmtClock, fmtTime, parseTs, syncClock, serverNow, toast, modal, esc, ApiError };
  window.api = api; window.poll = poll; window.fmtRemain = fmtRemain;
})();
