/* /admin/settings — 판정 기준값 편집 */
(function () {
  "use strict";
  const { api, toast, esc } = window.SS;
  const DEMO = { checkin_limit_min: 2, away_limit_min: 1, hoarding_min: 1 };
  const rows = document.getElementById("rows");
  let meta = {}, defaults = {};

  function render(settings) {
    rows.innerHTML = Object.keys(meta).map((k) => {
      const m = meta[k];
      return `<div class="setting-row">
        <div><div class="name"><label for="s-${k}" style="margin:0">${esc(m.label)}</label></div>
          <div class="desc">${esc(m.desc)} <span class="muted">(기본 ${defaults[k]}, 범위 ${m.min}~${m.max})</span></div></div>
        <div class="inp"><input type="number" id="s-${k}" name="${k}" min="${m.min}" max="${m.max}" step="1"
          value="${settings[k]}" inputmode="numeric" required><span class="muted">${esc(m.unit)}</span></div></div>`;
    }).join("");
  }

  function fill(values) {
    for (const [k, v] of Object.entries(values)) {
      const el = document.getElementById("s-" + k);
      if (el) { el.value = v; el.style.background = "#fff8d6"; }
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
      const v = document.getElementById("s-" + k).value.trim();
      if (!/^-?\d+$/.test(v)) { toast(`'${meta[k].label}' 값을 정수로 입력하세요.`, "error"); return; }
      body[k] = Number(v);
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
