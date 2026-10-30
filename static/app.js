const $ = (s) => document.querySelector(s),
  app = $("#app");
const params = new URLSearchParams(location.search);
let code = params.get("room")?.toUpperCase(),
  role = params.has("host") ? "host" : "person",
  token = code ? localStorage.getItem(`${code}:${role}`) : null,
  state,
  historyId = null;
const names = {
  done: "完成",
  working: "进行中",
  stuck: "卡住了",
  waiting: "未反馈",
};
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
function toast(s) {
  $("#toast")?.remove();
  const el = document.createElement("div");
  el.id = "toast";
  el.role = "alert";
  el.textContent = s;
  document.body.append(el);
  setTimeout(() => el.remove(), 5000);
}
async function api(path, body, method = "POST") {
  const r = await fetch("/api/rooms" + path, {
    method,
    headers: { "Content-Type": "application/json", Authorization: token || "" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    let d = await r.json().catch(() => ({ detail: "网络请求失败" }));
    throw Error(typeof d.detail === "string" ? d.detail : "输入格式无效");
  }
  return r.json();
}
function form(id, handler) {
  $(id)?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const b = e.target.querySelector("button");
    b.disabled = true;
    try {
      await handler(new FormData(e.target));
      e.target.reset();
      document.querySelector(id)?.reset();
    } catch (e) {
      toast(e.message);
    } finally {
      b.disabled = false;
    }
  });
}
function landing() {
  app.innerHTML = `<section class="hero"><div><span class="eyebrow">为一起动手的时刻而做</span><h1>讲到哪一步，<br>就看见哪一步。</h1><p class="muted">安装软件、实验带练、远程指导。把沉默变成明确的进度，让共同的问题先被看见。</p><span class="pill">免注册 · 手机参与 · 实时同步</span></div><div class="panel dark lead-demo"><span class="eyebrow muted">示意数据 / 当前步骤 02</span><h2>安装完成，并打开首页</h2><div class="demo-row"><span>✓ 已完成</span><strong>16</strong></div><div class="demo-row"><span>◷ 进行中</span><strong>5</strong></div><div class="demo-row"><span>! 卡住了</span><strong>3</strong></div><p class="muted">共同卡点：下载失败 · 3 人遇到</p></div></section><section class="forms"><div class="panel"><h2>我是讲解者</h2><p class="muted">创建临时房间，发布第一个操作步骤。</p><form id="create"><label for="title">本次带练主题</label><input id="title" name="title" maxlength="80" placeholder="例如：Python 环境安装" required><button>创建房间 ↗</button></form></div><div class="panel"><h2>我是参与者</h2><p class="muted">输入房间码，一次加入，跟随每一步。</p><form id="enter"><label for="room">6 位房间码</label><input id="room" name="room" maxlength="6" minlength="6" autocomplete="off" placeholder="例如：AB2345" required><button class="secondary">进入房间 →</button></form></div></section>`;
  form("#create", async (f) => {
    const r = await api("", { title: f.get("title") });
    localStorage.setItem(`${r.code}:host`, r.token);
    location.href = "/?room=" + r.code + "&host=1";
  });
  form("#enter", async (f) => {
    location.href =
      "/?room=" + encodeURIComponent(f.get("room").trim().toUpperCase());
  });
}
function join() {
  app.innerHTML = `<div class="panel"><span class="eyebrow">加入 ${esc(code)}</span><h1>让讲解者看见你的进度。</h1><p class="muted">昵称仅讲解者可见。请在同一浏览器持续使用，清除浏览器数据后会成为新的参与者。</p><form id="join"><label for="name">你的昵称</label><input name="name" id="name" maxlength="24" required autocomplete="nickname"><button>加入带练 →</button></form></div>`;
  form("#join", async (f) => {
    const r = await api("/" + code + "/join", { name: f.get("name") });
    token = r.token;
    localStorage.setItem(`${code}:person`, token);
    start();
  });
}
function render() {
  const s = historyId
      ? state.steps.find((s) => s.id === historyId)
      : state.steps.at(-1),
    isOld = !!historyId && s?.id !== state.steps.at(-1)?.id,
    locked = isOld || state.closed;
  // Preserve typed drafts and focus across incoming snapshots.
  const drafts = {};
  document.querySelectorAll("input").forEach((e) => (drafts[e.id] = e.value));
  const focus = document.activeElement?.id,
    sel = document.activeElement?.selectionStart;
  app.innerHTML = `<span class="eyebrow">${state.host ? "讲解者控制台" : "参与者"} / ${esc(code)} ${state.closed ? "· 已结束" : ""}</span><h1>${esc(state.title)}</h1><div class="layout"><div><div class="panel dark"><span class="eyebrow muted">${isOld ? "历史步骤 · 只读" : "当前操作步骤"} / ${state.steps.length ? state.steps.indexOf(s) + 1 : "等待发布"}</span><h2>${s ? esc(s.title) : "等待讲解者发布第一个步骤"}</h2><p class="muted">${s ? s.total : state.total} 人已加入${s ? ` · ${s.counts.done} 人完成` : ""}</p>${s ? `<progress max="${s.total || 1}" value="${s.counts.done}" aria-label="完成进度"></progress>` : ""}</div>${
    s
      ? `<div class="stats">${Object.keys(names)
          .map(
            (k) =>
              `<div class="stat ${k}"><strong>${s.counts[k]}</strong><span>${names[k]}</span></div>`,
          )
          .join(
            "",
          )}</div>${!state.host && !locked ? `<div class="status-buttons">${["working", "done", "stuck"].map((k) => `<button data-state="${k}" aria-pressed="${s.mine === k}" class="${s.mine === k ? "selected" : ""}">${names[k]}</button>`).join("")}</div><p class="muted">你当前：${names[s.mine]}。解决后请主动更新状态。</p>` : ""}${state.host ? `<div class="notice">${!s.total ? "等待参与者加入。" : s.counts.stuck ? "建议先处理共同卡点。" : s.counts.waiting ? "还有人未反馈，可先提醒一次。" : s.counts.working ? "有人仍在操作，建议稍等。" : "所有已加入的人均完成，可考虑推进。"} 状态来自主动反馈，不代表真实理解程度。</div>` : ""}<div class="panel"><h2>共同卡点 <span class="pill">本步骤</span></h2>${
          s.issues.length
            ? s.issues
                .sort((a, b) => a.resolved - b.resolved || b.count - a.count)
                .map(
                  (i) =>
                    `<div class="issue ${i.resolved ? "resolved" : ""}"><p>${esc(i.title)} <span class="pill">${i.count} 人遇到${i.resolved ? " · 已回应" : ""}</span></p>${!locked ? (state.host ? `<button class="secondary" data-issue="${i.id}" data-action="resolve" ${i.resolved ? "disabled" : ""}>标记已回应</button>` : `<button class="secondary" data-issue="${i.id}" data-action="${i.mine ? "withdraw" : "agree"}">${i.mine ? "撤回我也一样" : "我也一样"}</button>`) : ""}</div>`,
                )
                .join("")
            : '<p class="muted">暂时没有卡点。遇到问题就说出来。</p>'
        }${!state.host && !locked ? `<form id="issue"><label for="reason">描述卡点，避免填写密码等隐私信息</label><input id="reason" name="reason" maxlength="120" placeholder="例如：下载失败，错误代码 403" required><button class="secondary">提交卡点</button></form>` : ""}<small>讲解者标记已回应后，参与者仍需自行确认完成。</small></div>`
      : ""
  }${state.host && !state.closed ? `<div class="panel"><h2>发布${s ? "下一步" : "第一个步骤"}</h2><form id="step"><label for="stepTitle">要大家完成什么？</label><input id="stepTitle" name="title" maxlength="200" placeholder="例如：安装完成，并打开首页" required><button>发布步骤 →</button></form><p class="muted">发布后，所有参与者的新步骤状态从未反馈开始。</p></div>` : ""}</div><aside>${state.host ? `<div class="panel"><h3>邀请参与</h3><div class="code">${esc(code)}</div><img class="qr" src="/qr?code=${code}" alt="扫码加入当前房间"><button id="copy" class="secondary">复制参与链接</button><p class="muted">仅分享参与链接。讲解者凭证保存在本机浏览器，请勿清除。</p></div>` : ""}<div class="panel"><h3>步骤记录</h3><label for="history">查看本次进度</label><select id="history"><option value="">跟随当前步骤</option>${state.steps.map((x, i) => `<option value="${x.id}" ${historyId === x.id ? "selected" : ""}>${i + 1}. ${esc(x.title)}</option>`).join("")}</select></div>${state.host && s ? `<div class="panel"><h3>参与者 · ${s.total}</h3>${s.people.map((p) => `<div class="person"><span>${esc(p.name)} ${p.online ? "" : "· 离线"}</span><span>${names[p.state]}</span></div>`).join("") || '<p class="muted">等待加入</p>'}<small>离线不等于未完成；人数包含已加入的全部人。</small></div>` : ""}${state.host ? `<div class="panel actions"><button id="export" class="secondary">导出复盘 JSON</button>${!state.closed ? '<button id="close" class="secondary">结束带练</button>' : ""}<button id="delete" class="danger">删除房间数据</button></div>` : ""}</aside></div>`;
  Object.entries(drafts).forEach(([k, v]) => {
    const e = document.getElementById(k);
    if (e) e.value = v;
  });
  if (focus && document.getElementById(focus)) {
    const e = document.getElementById(focus);
    e.focus();
    if (e.setSelectionRange && sel !== null) e.setSelectionRange(sel, sel);
  }
  $("#history").onchange = (e) => {
    historyId = e.target.value || null;
    render();
  };
  form("#step", async (f) => {
    if (
      s &&
      (s.counts.stuck || s.counts.waiting || s.counts.working) &&
      !confirm("还有人未完成，确定发布下一步？")
    )
      return;
    await mutate("/steps", { title: f.get("title") });
  });
  form("#issue", async (f) =>
    mutate("/issues", { step: s.id, title: f.get("reason") }),
  );
  document
    .querySelectorAll("[data-state]")
    .forEach(
      (b) =>
        (b.onclick = () =>
          mutate("/status", { step: s.id, state: b.dataset.state })),
    );
  document
    .querySelectorAll("[data-issue]")
    .forEach(
      (b) =>
        (b.onclick = () =>
          mutate(`/issues/${b.dataset.issue}/${b.dataset.action}`, {})),
    );
  if ($("#copy"))
    $("#copy").onclick = async () => {
      const link = location.origin + "/?room=" + code;
      try {
        await navigator.clipboard.writeText(link);
        toast("参与链接已复制");
      } catch {
        prompt("复制此参与链接", link);
      }
    };
  if ($("#close"))
    $("#close").onclick = () => {
      if (confirm("结束后停止反馈，记录保留。确定结束？")) mutate("/close", {});
    };
  if ($("#delete"))
    $("#delete").onclick = async () => {
      if (confirm("永久删除本房间及所有记录？"))
        try {
          await api("/" + code, undefined, "DELETE");
          location.href = "/";
        } catch (e) {
          toast(e.message);
        }
    };
  if ($("#export"))
    $("#export").onclick = async () => {
      try {
        const r = await api("/" + code + "/export", undefined, "GET"),
          u = URL.createObjectURL(
            new Blob([JSON.stringify(r, null, 2)], {
              type: "application/json",
            }),
          ),
          a = document.createElement("a");
        a.href = u;
        a.download = code + "-复盘.json";
        a.click();
        setTimeout(() => URL.revokeObjectURL(u), 1000);
      } catch (e) {
        toast(e.message);
      }
    };
}
async function refresh() {
  state = await api("/" + code, undefined, "GET");
  render();
}
async function mutate(path, body) {
  try {
    await api("/" + code + path, body);
    await refresh();
  } catch (e) {
    toast(e.message);
  }
}
let generation = 0;
async function start() {
  const current = ++generation;
  try {
    await refresh();
  } catch (e) {
    app.innerHTML = `<div class="error">${esc(e.message)} <a href="/">返回首页</a></div>`;
    return;
  }
  if (!state.host) {
    const beat = () => api("/" + code + "/heartbeat", {}).catch(() => {});
    beat();
    setInterval(beat, 15000);
  }
  while (current === generation) {
    try {
      const r = await fetch("/api/rooms/" + code + "/events", {
        headers: { Authorization: token },
      });
      if (!r.ok) throw Error("连接失败");
      $("#connection").textContent = "● 实时连接";
      const reader = r.body.getReader(),
        decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          if (frame.startsWith("event: gone")) {
            app.innerHTML =
              '<div class="error">房间已删除。<a href="/">返回首页</a></div>';
            return;
          }
          if (frame.startsWith("data: ")) {
            state = JSON.parse(frame.slice(6));
            render();
          }
        }
      }
    } catch {}
    $("#connection").textContent = "连接中断，正在重连…";
    await new Promise((r) => setTimeout(r, 2500));
  }
}
if (!code) landing();
else if (token) start();
else if (role === "host") {
  app.innerHTML =
    '<div class="error">当前浏览器没有讲解者凭证。请使用创建房间的浏览器。</div>';
} else join();
