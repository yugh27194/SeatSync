/* /admin/settings — 판정 기준값 편집. 분 단위 값은 15초 단위(분 + 0/15/30/45초)로 정한다. */
(function () {
  "use strict";
  const { api, toast, esc, fmtMin } = window.SS;
  // 시연(약 3분)용: 체크인 1분 · 이탈/사석화 30초 · 확인 필요 후 자동 반납 30초 · 사전 경고 15초 전
  const DEMO = { checkin_limit_min: 1, away_limit_min: 0.5, hoarding_min: 0.5, auto_return_min: 0.5,
    prewarn_min: 0.25, waitlist_hold_min: 1 };
  const rows = document.getElementById("rows");
  let meta = {}, defaults = {};
  const isTime = (k) => meta[k] && meta[k].unit === "분";
  const fmt = (k, v) => (isTime(k) ? fmtMin(v) : `${v}${meta[k].unit}`);

  function input(k, v) {
    const m = meta[k];
    if (!isTime(k)) {
      return `<input type="number" id="s-${k}" min="${m.min}" max="${m.max}" step="1" value="${v}" inputmode="numeric" required>
        <span class="muted">${esc(m.unit)}</span>`;
    }
    const total = Math.round(Number(v) * 60);
    const mm = Math.floor(total / 60), ss = total % 60;
    return `<input type="number" id="s-${k}-m" min="0" max="${Math.floor(m.max)}" step="1" value="${mm}" inputmode="numeric" required style="max-width:80px">
      <span class="muted">분</span>
      <select id="s-${k}-s" style="max-width:80px">${[0, 15, 30, 45].map((s) => `<option value="${s}"${s === ss ? " selected" : ""}>${s}</option>`).join("")}</select>
      <span class="muted">초</span>`;
  }

  function render(settings) {
    rows.innerHTML = Object.keys(meta).map((k) => {
      const m = meta[k];
      return `<div class="setting-row">
        <div><div class="name"><label for="s-${k}${isTime(k) ? "-m" : ""}" style="margin:0">${esc(m.label)}</label></div>
          <div class="desc">${esc(m.desc)} <span class="muted">(기본 ${fmt(k, defaults[k])}, 범위 ${fmt(k, m.min)}~${fmt(k, m.max)})</span></div></div>
        <div class="inp">${input(k, settings[k])}</div></div>`;
    }).join("");
  }

  function read(k) {
    if (!isTime(k)) {
      const v = document.getElementById("s-" + k).value.trim();
      return /^-?\d+$/.test(v) ? Number(v) : NaN;
    }
    const m = document.getElementById(`s-${k}-m`).value.trim();
    if (!/^\d+$/.test(m)) return NaN;
    return Number(m) + Number(document.getElementById(`s-${k}-s`).value) / 60;
  }

  function fill(values) {
    for (const [k, v] of Object.entries(values)) {
      if (!meta[k]) continue;
      const el = document.querySelector(`#s-${k}, #s-${k}-m`);
      if (!el) continue;
      el.closest(".inp").innerHTML = input(k, v);
      document.querySelectorAll(`#s-${k}, #s-${k}-m, #s-${k}-s`).forEach((x) => { x.style.background = "#fff8d6"; });
    }
  }

  async function load() {
    const d = await api("GET", "/api/admin/settings");
    meta = d.meta; defaults = d.defaults;
    render(d.settings);
  }

  document.getElementById("settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {};
    for (const k of Object.keys(meta)) {
      const v = read(k);
      if (Number.isNaN(v)) { toast(`'${meta[k].label}' 값을 숫자로 입력하세요.`, "error"); return; }
      // 15초 단위 → 분(소수 둘째 자리까지 정확: 0.25 단위)
      body[k] = isTime(k) ? Math.round(v * 4) / 4 : v;
    }
    try {
      const d = await api("PUT", "/api/admin/settings", body);
      render(d.settings);
      toast("저장했습니다. 다음 갱신부터 반영됩니다.", "ok");
    } catch (err) { /* 토스트 표시됨 */ }
  });
  document.getElementById("btn-demo").onclick = () => { fill(DEMO); toast("시연 모드 값을 채웠습니다. [저장]을 눌러 적용하세요."); };
  document.getElementById("btn-default").onclick = () => { fill(defaults); toast("기본값을 채웠습니다. [저장]을 눌러 적용하세요."); };

  load();
})();
