const $ = (s, r = document) => r.querySelector(s),
  app = $("#app"),
  query = new URLSearchParams(location.search);
let code = (query.get("room") || "").toUpperCase(),
  role = query.get("role") || (query.has("host") ? "host" : "person"),
  token = code ? localStorage.getItem(`${code}:${role}`) : null;
const hash = new URLSearchParams(location.hash.slice(1));
if (code && hash.has("access")) {
  token = hash.get("access");
  localStorage.setItem(`${code}:${role}`, token);
  history.replaceState(null, "", location.pathname + location.search);
}
let state,
  page = "live",
  historyId = "",
  editing = "",
  filter = "all",
  connected = false,
  writing = false,
  streamController,
  heartbeatTimer,
  generation = 0,
  lastSignature = "",
  previousCurrent,
  previousAlert = "";
const pending = new Map(),
  labels = {
    done: "完成",
    working: "进行中",
    stuck: "卡住了",
    waiting: "未反馈",
  },
  stages = {
    open: "待处理",
    working: "处理中",
    answered: "已发布办法",
    closed: "已关闭",
  };
const esc = (x) =>
  String(x ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const button = (label, action, id = "", kind = "secondary") =>
  `<button type="button" class="${kind}" data-action="${action}" data-id="${esc(id)}">${label}</button>`;
const empty = (s) => `<p class="empty">${s}</p>`;
function helpLink(url) {
  try {
    const u = new URL(url);
    if (["http:", "https:"].includes(u.protocol))
      return `<a class="help-link" href="${esc(u.href)}" target="_blank" rel="noopener noreferrer">打开帮助 ↗</a>`;
  } catch {}
  return "";
}
function toast(message) {
  $("#toast")?.remove();
  const e = document.createElement("div");
  e.id = "toast";
  e.role = "alert";
  e.textContent = message;
  document.body.append(e);
  setTimeout(() => e.remove(), 6500);
}
function connection(text, ok = false) {
  connected = ok;
  $("#connection").textContent = text;
  document
    .querySelectorAll("[data-needs-connection]")
    .forEach((e) => (e.disabled = !ok || writing || state?.closed));
}
async function request(path, body, method = "POST") {
  const ctl = new AbortController(),
    timer = setTimeout(() => ctl.abort(), 12000);
  try {
    const r = await fetch("/api/rooms" + path, {
      method,
      headers: {
        "Content-Type": "application/json",
        Authorization: token || "",
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctl.signal,
    });
    const d = await r.json().catch(() => ({ detail: `服务返回 ${r.status}` }));
    if (!r.ok) {
      const e = Error(
        typeof d.detail === "string" ? d.detail : "输入内容不符合要求",
      );
      e.status = r.status;
      throw e;
    }
    return d;
  } catch (e) {
    if (e.name === "AbortError")
      throw Error("请求超时，输入已保留；重试不会重复提交");
    throw e;
  } finally {
    clearTimeout(timer);
  }
}
async function refresh() {
  state = await request("/" + code, undefined, "GET");
  connection("● 已同步", true);
  render();
}
async function command(action, data = {}) {
  if (writing) throw Error("上一项操作正在提交");
  const key = JSON.stringify([action, data]),
    id =
      pending.get(key) ||
      (crypto.randomUUID
        ? crypto.randomUUID()
        : Array.from(crypto.getRandomValues(new Uint8Array(16)), (x) =>
            x.toString(16).padStart(2, "0"),
          ).join(""));
  pending.set(key, id);
  writing = true;
  connection("正在提交…", connected);
  try {
    const result = await request("/" + code + "/commands", {
      action,
      data,
      request_id: id,
    });
    pending.delete(key);
    try {
      await refresh();
    } catch {
      connection("操作已保存，正在重新同步…");
      toast("操作已保存，连接恢复后更新界面");
    }
    return result;
  } catch (e) {
    if (e.status === 409) {
      pending.delete(key);
      await refresh().catch(() => {});
    }
    throw e;
  } finally {
    writing = false;
    document
      .querySelectorAll("[data-needs-connection]")
      .forEach((e) => (e.disabled = !connected || state?.closed));
  }
}
function bindForm(id, handler) {
  const f = $(id);
  if (!f) return;
  f.onsubmit = async (e) => {
    e.preventDefault();
    if (writing) return;
    const b = f.querySelector("[type=submit]");
    if (b) b.disabled = true;
    try {
      const result = await handler(new FormData(f));
      if (result !== false) {
        document.querySelector(id)?.reset();
        f.reset();
      }
    } catch (e) {
      toast(e.message);
    } finally {
      if (b) b.disabled = false;
    }
  };
}
function download(name, value, type = "application/json") {
  const u = URL.createObjectURL(
      new Blob(
        [typeof value === "string" ? value : JSON.stringify(value, null, 2)],
        { type },
      ),
    ),
    a = document.createElement("a");
  a.href = u;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(u), 1000);
}
function showShare(title, value) {
  $("#share-dialog")?.remove();
  const d = document.createElement("dialog");
  d.id = "share-dialog";
  d.innerHTML = `<h2>${esc(title)}</h2><p>仅将此链接交给对应身份的人，请勿公开管理凭证。</p><textarea readonly aria-label="分享内容">${esc(value)}</textarea><div class="actions">${button("复制", "clipboard")}${button("关闭", "dismiss")}</div>`;
  document.body.append(d);
  d.showModal();
  $("[data-action=clipboard]", d).onclick = () =>
    navigator.clipboard
      .writeText(value)
      .then(() => toast("已复制"))
      .catch(() => toast("请选中文本手动复制"));
  $("[data-action=dismiss]", d).onclick = () => d.close();
}
function landing() {
  app.innerHTML = `<section class="hero"><div><span class="eyebrow">WORKSHOP PULSE / v0.3</span><h1>一起往前，<br>也看见掉队的人。</h1><p class="muted">每步报进度，共同问题一起处理。发布办法后，让参与者亲自确认恢复。</p><span class="pill">免注册 · 可自部署 · 助教协作</span></div><div class="panel dark lead-demo"><span class="eyebrow muted">工作方式示意</span><h2>安装完成，并打开首页</h2><div class="demo-row"><span>全班当前步骤</span><strong>03</strong></div><div class="demo-row"><span>下载失败 · 5 人遇到</span><strong>3 人恢复</strong></div><p class="muted">全班进入下一步，仍受阻的 2 人继续留在跟进列表。</p></div></section><section class="forms"><div class="panel"><h2>创建一次带练</h2><form id="create"><label for="title">主题</label><input id="title" name="title" required maxlength="80" placeholder="例如：Python 环境安装"><label for="expected">预计人数（可选）</label><input id="expected" name="expected" type="number" min="0" max="200" value="0"><button type="submit">创建房间 →</button></form></div><div class="panel"><h2>加入带练</h2><form id="enter"><label for="room-code">6 位房间码</label><input id="room-code" name="code" required minlength="6" maxlength="6"><button type="submit" class="secondary">进入参与页 →</button></form><details><summary>恢复主持 / 助教 / 参与身份</summary><form id="recover"><label for="recover-code">房间码</label><input id="recover-code" name="code" required maxlength="6"><label for="recovery-token">恢复凭证</label><input id="recovery-token" name="token" type="password" required autocomplete="off"><button type="submit" class="secondary">验证并恢复</button></form></details></div></section>`;
  bindForm("#create", async (f) => {
    const r = await request("", {
      title: f.get("title"),
      expected: Number(f.get("expected") || 0),
    });
    localStorage.setItem(`${r.code}:host`, r.token);
    location.href = `/?room=${r.code}&host=1`;
  });
  bindForm("#enter", async (f) => {
    location.href =
      "/?room=" + encodeURIComponent(f.get("code").trim().toUpperCase());
  });
  bindForm("#recover", async (f) => {
    const room = f.get("code").trim().toUpperCase(),
      credential = f.get("token").trim(),
      r = await request("/" + room + "/recover", { token: credential });
    localStorage.setItem(`${room}:${r.role}`, credential);
    location.href = `/?room=${room}&role=${r.role}`;
  });
}
function join() {
  app.innerHTML = `<div class="panel join-card"><span class="eyebrow">加入 ${esc(code)}</span><h1>每一步，给一个回应。</h1><p class="muted">昵称和座位号仅讲解者与助教可见；不填写则自动分配别名。</p><form id="join"><label for="name">昵称（可选）</label><input id="name" name="name" maxlength="24"><label for="seat">座位号（可选）</label><input id="seat" name="seat" maxlength="16" placeholder="例如 A03 或 12"><button type="submit">加入带练 →</button></form></div>`;
  bindForm("#join", async (f) => {
    const r = await request("/" + code + "/join", {
      name: f.get("name"),
      seat: f.get("seat"),
    });
    token = r.token;
    localStorage.setItem(`${code}:person`, token);
    await start();
  });
}
function current() {
  return state.steps.find((s) => s.id === state.current);
}
function allIssues() {
  return state.steps.flatMap((s) =>
    s.issues.map((i) => ({
      ...i,
      step: s.id,
      stepTitle: s.title,
      old: s.phase === "frozen",
    })),
  );
}
function stats(s) {
  return `<div class="stats">${Object.entries(labels)
    .map(
      ([k, v]) =>
        `<div class="stat ${k}"><strong>${s.counts[k]}</strong><span>${v}</span></div>`,
    )
    .join("")}</div>`;
}
function taskCard(s) {
  return `<div class="panel dark current-card"><span class="eyebrow muted">${s.phase === "frozen" ? "历史检查点 · 推进时快照" : "当前检查点"}</span><h2>${esc(s.title)}</h2>${s.criteria ? `<p class="criteria">完成标准：${esc(s.criteria)}</p>` : ""}${helpLink(s.link)}${s.tips ? `<details><summary>带练提示</summary><p>${esc(s.tips)}</p></details>` : ""}</div>`;
}
function answerCard(a) {
  return `<div class="answer"><span class="eyebrow">处理办法 ${a.public ? "· 可投屏" : ""}</span><p>${esc(a.body)}</p>${helpLink(a.link)}${state?.host ? button("存为步骤经验", "save-tip", a.id) : ""}</div>`;
}
function issueCardBase(i, staff) {
  let controls = "";
  if (staff && !state.closed) {
    const roles = (state.roles || []).filter(
        (r) => r.role === "helper" && !r.revoked,
      ),
      owner =
        i.assignee === "host"
          ? "讲解者"
          : roles.find((x) => x.id === i.assignee)?.name;
    controls = `<p class="muted">${owner ? "认领：" + esc(owner) : "尚未认领"} · ${stages[i.stage] || i.stage}</p><div class="actions">${button(i.assignee === state.me.id ? "释放任务" : "我来处理", i.assignee === state.me.id ? "unclaim" : "claim", i.id)}${state.host ? `<select aria-label="分配此问题" data-assign="${i.id}"><option value="">分配给…</option><option value="host">讲解者</option>${roles.map((r) => `<option value="${r.id}">${esc(r.name)}</option>`).join("")}</select>` : ""}${button("发布办法", "answer", i.id)}${!i.affected && i.stage !== "closed" ? button("关闭卡点", "issue-close", i.id) : ""}${state.host ? button("合并同类", "merge", i.id) : ""}</div>`;
  } else if (!staff && !state.closed) {
    controls = `<div class="actions">${!i.mine && !i.old ? button("我也一样", "agree", i.id) : ""}${i.mine ? (i.my_active ? button("已解决，继续操作", "recover", i.id) : button("仍然卡住", "still", i.id)) : ""}${i.mine ? button("退出此卡点", "withdraw", i.id) : ""}</div>`;
  }
  return `<article class="issue-card"><div class="section-head"><h3>${esc(i.title)}</h3>${i.old ? '<span class="pill warning">前序问题</span>' : ""}</div><div class="issue-counts"><span>累计 ${i.count} 人遇到</span><b>${i.affected} 人仍受影响</b><span>${i.recovered} 人恢复</span></div>${i.sources?.length ? `<small>合并来源：${i.sources.map(esc).join("、")}</small>` : ""}${i.answers.map(answerCard).join("")}${controls}${staff && i.members?.length ? `<details><summary>受影响者与恢复情况</summary>${i.members.map((m) => `<div class="person"><span>${esc(m.name)}${m.left_at ? " · 已离开" : ""}</span><span>${m.active ? "受影响" : "已恢复"}</span></div>`).join("")}</details>` : ""}${i.old ? `<small>来源：${esc(i.stepTitle)}</small>` : ""}</article>`;
}
function participantView() {
  const s = historyId ? state.steps.find((x) => x.id === historyId) : current(),
    old = s?.phase === "frozen";
  let html = s
    ? taskCard(s)
    : `<div class="panel dark"><h2>${state.closed ? "本次带练已结束" : "等待讲解者发布步骤"}</h2><p>页面会自动同步。</p></div>`;
  html = environmentWidget() + html;
  if (s && !old && !state.closed)
    html += `<section class="participant-actions"><div class="status-buttons">${["working", "done", "stuck"].map((k) => `<button data-action="state" data-id="${k}" data-needs-connection aria-pressed="${s.mine === k}" class="${s.mine === k ? "selected" : ""}">${k === "working" ? "◷" : k === "done" ? "✓" : "!"} ${labels[k]}</button>`).join("")}</div><p class="feedback-line" role="status">你当前：<b>${labels[s.mine]}</b> · ${connected ? "服务端已同步" : "等待重新同步"}</p></section>`;
  if (s) html += checkpointExtras(s);
  const tails = (state.tails || []).filter((t) => !t.recovered);
  if (tails.length)
    html += `<section class="panel"><h2>前序步骤待确认</h2>${tails.map((t) => `<div class="issue"><p>${esc(t.title)}</p>${button("已能继续跟上", "catchup", t.step)}</div>`).join("")}<small>仅记录恢复，不改变已归档的完成统计。具体卡点仍需分别确认解决。</small></section>`;
  const backlog = allIssues().filter((i) => i.mine && i.my_active && i.old);
  if (backlog.length)
    html += `<section class="panel"><h2>前序问题仍在跟进 <span class="pill warning">${backlog.length}</span></h2><p class="muted">讲解者已推进，恢复后请确认。</p>${backlog.map((i) => issueCard(i, false)).join("")}</section>`;
  if (s)
    html += `<section class="panel"><h2>${old ? "这个检查点的卡点" : "本步共同卡点"}</h2>${s.issues.map((i) => issueCard({ ...i, old, stepTitle: s.title }, false)).join("") || empty("暂无卡点。遇到问题可以先点「卡住了」。")}${!old && !state.closed ? `<div class="reason-chips">${s.reasons.map((r, n) => button(esc(r), "reason", String(n))).join("")}</div><form id="issue-form"><label for="issue-title">补充卡点（不要输入密码等信息）</label><input id="issue-title" name="title" maxlength="120" required><button type="submit" data-needs-connection>提交卡点</button></form>` : ""}</section><details class="panel"><summary>本步全班反馈</summary>${stats(s)}<p class="muted">分母 ${s.total} 人；沉默始终记为未反馈。</p></details>`;
  html += `<details class="panel"><summary>步骤记录与个人设置</summary><label for="history">查看检查点</label><select id="history"><option value="">跟随当前步骤</option>${state.steps.map((x) => `<option value="${x.id}" ${historyId === x.id ? "selected" : ""}>${esc(x.title)}</option>`).join("")}</select><form id="seat-form"><label for="my-seat">座位号</label><input id="my-seat" name="seat" maxlength="16" value="${esc(state.me?.seat)}"><button type="submit" class="secondary">更新座位号</button></form><div class="actions">${button("保存身份恢复凭证", "save-credential")}${button("开启浏览器提醒", "notifications")}${button("离开本次带练", "leave", "", "danger")}</div></details>`;
  return `<div class="participant-shell">${html}</div>`;
}
function screenView() {
  const s = current() || state.steps.filter((x) => x.phase === "frozen").at(-1);
  return `<div class="screen-shell">${s ? taskCard(s) + stats(s) : empty("等待发布当前步骤")}<p class="screen-cohort">${state.expected ? "预计 " + state.expected + " 人 · " : ""}${state.total} 人有效加入${state.missing ? " · 尚差 " + state.missing + " 人" : ""}</p>${s?.issues.length ? `<div class="panel"><h2>公开帮助信息</h2>${s.issues.map(answerCard).join("")}</div>` : ""}<p class="muted">仅显示汇总与讲解者主动公开的信息。</p></div>`;
}
function roster(s) {
  const visible = (s?.people || []).filter(
    (p) =>
      filter === "all" ||
      (filter === "offline" ? !p.online : p.state === filter),
  );
  return `<section class="panel"><div class="section-head"><h2>人员与座位</h2><select id="roster-filter" aria-label="筛选人员"><option value="all">全部</option>${Object.entries(
    labels,
  )
    .map(
      ([k, v]) =>
        `<option value="${k}" ${filter === k ? "selected" : ""}>${v}</option>`,
    )
    .join(
      "",
    )}<option value="offline" ${filter === "offline" ? "selected" : ""}>离线</option></select></div><p class="muted">按座位号排列；离线不会自动变成离开。</p><div class="seat-map">${
    visible
      .sort((a, b) =>
        (a.seat || "~").localeCompare(b.seat || "~", "zh-CN", {
          numeric: true,
        }),
      )
      .map(
        (p) =>
          `<div class="seat ${p.state}"><strong>${esc(p.seat || "未填座位")}</strong><span>${esc(p.name)}</span><small>${labels[p.state]}${p.online ? "" : " · 离线"}</small>${state.host ? button("移除", "remove", p.id) : ""}</div>`,
      )
      .join("") || empty("没有符合筛选条件的成员")
  }</div>${state.departed?.length ? `<details><summary>已离开 / 移除 ${state.departed.length} 人</summary>${state.departed.map((p) => `<p>${esc(p.name)} · ${esc(p.seat)}</p>`).join("")}</details>` : ""}</section>`;
}
function staffLive() {
  const s = historyId ? state.steps.find((x) => x.id === historyId) : current(),
    backlog = allIssues().filter((i) => i.old && i.affected > 0),
    next = state.steps.find((x) => x.phase === "draft");
  return `<div class="layout"><div>${s ? taskCard(s) + stats(s) : '<div class="panel dark"><h2>还没有开始带练</h2><p>编排步骤，或直接发布第一个检查点。</p></div>'}<div class="cohort-strip"><span>预计 <b>${state.expected || "未设置"}</b></span><span>有效加入 <b>${state.total}</b></span><span>累计加入 <b>${state.joined_total}</b></span>${state.missing ? `<span>尚差 <b>${state.missing}</b></span>` : ""}</div>${s ? `<div class="notice">${!s.total ? "等待参与者加入。" : s.counts.stuck ? "建议优先处理受阻问题。" : s.counts.waiting ? "还有人未反馈，先确认情况。" : s.counts.working ? "有人仍在操作，可以稍等。" : "当前参与范围已全部自报完成。"}${backlog.length ? ` 另有 ${backlog.length} 个前序问题。` : ""}</div><section class="panel"><h2>当前检查点的问题</h2>${s.issues.map((i) => issueCard({ ...i, old: s.phase === "frozen", stepTitle: s.title }, true)).join("") || empty("暂无卡点")}</section>` : ""}${backlog.length ? `<section class="panel"><h2>前序仍需协助</h2>${backlog.map((i) => issueCard(i, true)).join("")}</section>` : ""}${
    (state.tails || []).some((t) => !t.recovered)
      ? `<section class="panel"><h2>前序步骤跟进</h2>${state.tails
          .filter((t) => !t.recovered)
          .map(
            (t) =>
              `<div class="person"><span>${esc(t.name)}</span><span>${esc(t.title)}</span></div>`,
          )
          .join("")}<small>由参与者主动确认已能继续跟上。</small></section>`
      : ""
  }${s?checkpointExtras(s):""}${roster(s)}</div><aside>${state.host && !state.closed ? `<section class="panel"><h3>推进带练</h3>${next ? `<p>${esc(next.title)}</p>${button("发布下一步 →", "start-step", next.id, "primary")}` : empty("没有待发布步骤")}<details><summary>临场直接发布</summary>${stepForm("quick-step", "发布新步骤 →")}</details><div class="actions">${button("结束本次带练", "close")}</div></section>` : ""}<section class="panel"><h3>节奏与提醒</h3><div class="timer" id="countdown">${timerText()}</div>${state.host && !state.closed ? `<div class="actions">${button("2 分钟", "timer", "120")}${button("5 分钟", "timer", "300")}${button("10 分钟", "timer", "600")}${button("停止", "timer", "0")}</div>` : ""}${button("开启浏览器提醒", "notifications")}<small>默认关闭。计时结束不会自动推进。</small></section><section class="panel"><h3>历史检查点</h3><select id="history" aria-label="查看历史检查点"><option value="">跟随当前步骤</option>${state.steps
    .filter((x) => x.phase !== "draft")
    .map(
      (x) =>
        `<option value="${x.id}" ${historyId === x.id ? "selected" : ""}>${esc(x.title)}</option>`,
    )
    .join("")}</select></section></aside></div>`;
}
function stepForm(id, submit = "保存待发布步骤", s = {}) {
  return `<form id="${id}"><label for="${id}-title">步骤标题</label><input id="${id}-title" name="title" required maxlength="200" value="${esc(s.title || "")}"><label for="${id}-criteria">完成标准</label><textarea id="${id}-criteria" name="criteria" maxlength="1000" placeholder="例如：终端运行 python --version，看到版本号">${esc(s.criteria || "")}</textarea><label for="${id}-link">帮助链接（可选）</label><input id="${id}-link" name="link" type="url" maxlength="1000" value="${esc(s.link || "")}"><label for="${id}-reasons">常见卡点（每行一个，最多 12 条）</label><textarea id="${id}-reasons" name="reasons" maxlength="1452">${esc((s.reasons || []).join("\n"))}</textarea><label for="${id}-tips">带练提示 / 可复用的处理经验</label><textarea id="${id}-tips" name="tips" maxlength="2000">${esc(s.tips || "")}</textarea>${checkpointOptionsForm(id,s)}<button type="submit" data-needs-connection>${submit}</button></form>`;
}
function planning() {
  const edit = state.steps.find((s) => s.id === editing);
  return `<div class="layout"><section><h2>检查点编排</h2><p class="muted">已发布步骤保留原貌，待发布步骤可编辑、排序和删除。</p>${state.steps.map((s) => `<article class="panel plan-step"><span class="pill">${s.phase === "draft" ? "待发布" : s.phase === "active" ? "正在进行" : "已归档"}</span><h3>${esc(s.title)}</h3><p>${esc(s.criteria || "未填写完成标准")}</p>${s.phase === "draft" && !state.closed ? `<div class="actions">${button("编辑", "edit-step", s.id)}${button("上移", "move-up", s.id)}${button("下移", "move-down", s.id)}${button("发布", "start-step", s.id)}${button("删除", "delete-step", s.id)}</div>` : ""}</article>`).join("") || empty("还没有步骤，可手动新增或导入模板。")}</section><aside><section class="panel"><h3>${edit ? "编辑检查点" : "新增检查点"}</h3>${!state.closed ? stepForm("plan-form", edit ? "保存修改" : "加入待发布列表", edit || {}) : empty("已结束，不能再修改")}${editing ? button("取消编辑", "cancel-edit") : ""}</section><section class="panel"><h3>模板与复用</h3><div class="actions">${button("下载本次模板", "template-export")}${button("复制为新场次", "copy-room")}</div>${!state.closed ? `<label for="template-file">导入模板 JSON（追加步骤）</label><input type="file" id="template-file" accept="application/json,.json"><a href="/examples/python-setup.json" download>下载 Python 安装示例</a>` : ""}<p class="muted">只带走步骤和帮助信息，不含参与者、凭证和旧求助记录。</p></section></aside></div>`;
}
function collaboration() {
  return `<div class="forms"><section class="panel"><h2>邀请参与者</h2><div class="code">${esc(code)}</div><img class="qr" src="/qr?code=${code}" alt="参与者扫码加入"><div class="actions">${button("复制参与链接", "invite")}${button("开启匿名投屏", "new-screen")}</div><p class="muted">投屏使用独立、可撤销的只读身份。</p></section><section class="panel"><h2>助教与投屏权限</h2>${!state.closed ? `<form id="helper-form"><label for="helper-name">助教称呼</label><input id="helper-name" name="name" maxlength="24"><button type="submit" data-needs-connection>生成助教邀请</button></form>` : ""}${
    state.roles
      ?.filter((r) => !r.revoked)
      .map(
        (r) =>
          `<div class="person"><span>${esc(r.name)} · ${r.role === "helper" ? "助教" : "只读投屏"}</span>${button("撤销", "revoke", r.id)}</div>`,
      )
      .join("") || empty("暂未发放协作权限")
  }<p class="muted">助教可认领、回答问题，不能推进、删除房间或替参与者完成。</p></section></div>`;
}
function summaryView() {
  const rows = state.steps.filter((s) => s.phase !== "draft");
  return `<div class="section-head"><div><h2>本次带练复盘</h2><p class="muted">${state.closed ? "已结束" : "进行中"} · 历史统计为推进时快照。</p></div><div class="actions">${button("导出 CSV", "csv")}${button("导出 JSON", "json")}${button("打印 / 保存 PDF", "print")}</div></div>${rows.map((s) => `<section class="panel"><div class="section-head"><h3>${esc(s.title)}</h3><span class="pill">${s.opened ? Math.max(0, Math.round(((s.ended || Date.now() / 1000) - s.opened) / 60)) + " 分钟" : "未开始"}</span></div><p>${esc(s.criteria)}</p>${stats(s)}<p class="muted">分母 ${s.total} 人 · ${s.phase === "frozen" ? "推进时冻结" : "当前反馈"}</p>${s.issues.map((i) => `<div class="issue"><h3>${esc(i.title)}</h3><p>累计 ${i.count} 人 · 仍受影响 ${i.affected} 人 · 自报恢复 ${i.recovered} 人</p>${i.answers.map(answerCard).join("") || "<small>尚无文字处理办法</small>"}</div>`).join("")}</section>`).join("") || empty("发布第一个步骤后生成记录")}<div class="notice">尚有 ${allIssues().filter((i) => i.affected).length} 个问题影响有效成员。恢复和完成是不同的自报信号。</div>`;
}
function settings() {
  return `<div class="forms"><section class="panel"><h2>人数与全班公告</h2><form id="settings-form"><label for="room-expected">预计人数</label><input id="room-expected" name="expected" type="number" min="0" max="200" value="${state.expected}"><label for="announcement">公告（所有人可见）</label><textarea id="announcement" name="announcement" maxlength="500">${esc(state.announcement)}</textarea><button type="submit" data-needs-connection>保存设置</button></form></section><section class="panel"><h2>房间与数据</h2><p>主持恢复凭证相当于本房间钥匙，请单独保管。</p><div class="actions">${button("保存主持恢复凭证", "save-credential")}${!state.closed ? button("结束带练", "close") : ""}${button("永久删除本房间", "delete-room", "", "danger")}</div><p class="muted">不会自动清理房间。导出后可主动删除，备份需单独处理。</p></section></div>`;
}
function timerText() {
  if (!state?.timer_end) return "未设置计时";
  const n = Math.max(0, Math.ceil(state.timer_end - Date.now() / 1000));
  return n
    ? `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`
    : "计时结束";
}
function render(preserve = true) {
  if (!state) return;
  const saved = {};
  if (preserve)
    document
      .querySelectorAll("input:not([type=file]),textarea,select")
      .forEach((e) => (saved[e.id] = e.value));
  const opened = [...document.querySelectorAll("details[open]")].map(
      (e) => e.querySelector("summary")?.textContent,
    ),
    focus = document.activeElement?.id,
    selection = document.activeElement?.selectionStart;
  const staff = ["host", "helper"].includes(state.role);
  if (state.role !== "host") page = "live";
  const views = {
    live: staffLive,
    plan: planning,
    team: collaboration,
    summary: summaryView,
    settings,
  };
  app.innerHTML = `<div class="room-heading"><div><span class="eyebrow">${{ host: "讲解者控制台", helper: "助教工作台", person: "参与者", screen: "匿名投屏" }[state.role]} / ${esc(code)}</span><h1>${esc(state.title)}</h1></div><span class="pill">${state.closed ? "已结束 · 记录只读" : "带练进行中"}</span></div>${state.announcement ? `<div class="announcement" role="status">公告：${esc(state.announcement)}</div>` : ""}${state.timer_end && state.role === "person" ? `<p class="muted">本轮剩余 <span id="countdown">${timerText()}</span></p>` : ""}${
    state.host
      ? `<nav class="tabs" aria-label="房间导航">${[
          ["live", "现场进度"],
          ["plan", "步骤与模板"],
          ["team", "邀请与协作"],
          ["summary", "复盘与导出"],
          ["settings", "设置"],
        ]
          .map(
            ([id, label]) =>
              `<button data-page="${id}" class="${page === id ? "active" : ""}" aria-current="${page === id ? "page" : "false"}">${label}</button>`,
          )
          .join("")}</nav>`
      : ""
  }${state.role === "screen" ? screenView() : staff ? views[page]() : participantView()}`;
  Object.entries(saved).forEach(([id, value]) => {
    const el = document.getElementById(id);
    if (el) el.value = value;
  });
  document.querySelectorAll("details").forEach((e) => {
    if (opened.includes(e.querySelector("summary")?.textContent)) e.open = true;
  });
  if (focus && document.getElementById(focus)) {
    const el = document.getElementById(focus);
    el.focus({ preventScroll: true });
    if (el.setSelectionRange && selection !== null)
      try {
        el.setSelectionRange(selection, selection);
      } catch {}
  }
  bind();
  document
    .querySelectorAll("[data-needs-connection]")
    .forEach((e) => (e.disabled = !connected || writing || state.closed));
}
function dataFromStep(f) {
  return {
    title: f.get("title"),
    criteria: f.get("criteria"),
    link: f.get("link"),
    tips: f.get("tips"),
    options: readCheckpointOptions(f),
    reasons: f
      .get("reasons")
      .split("\n")
      .map((x) => x.trim())
      .filter(Boolean),
  };
}
function advanceWarning() {
  const s = current(),
    backlog = allIssues().some((i) => i.old && i.affected);
  return (
    !s ||
    !(s.counts.stuck + s.counts.working + s.counts.waiting || backlog) ||
    confirm("仍有人未完成或有前序待办。发布后保留卡点与跟进记录，确定继续？")
  );
}
function bind() {
  bindProgressForms();
  bindForm("#environment-form", async f=>command("person.environment",{environment:f.get("environment")}));
  document.querySelectorAll("[data-page]").forEach(
    (b) =>
      (b.onclick = () => {
        page = b.dataset.page;
        historyId = "";
        render();
      }),
  );
  if ($("#history"))
    $("#history").onchange = (e) => {
      historyId = e.target.value;
      render();
    };
  if ($("#roster-filter"))
    $("#roster-filter").onchange = (e) => {
      filter = e.target.value;
      render();
    };
  document.querySelectorAll("[data-action]").forEach(
    (b) =>
      (b.onclick = async () => {
        try {
          await act(b.dataset.action, b.dataset.id);
        } catch (e) {
          toast(e.message);
        }
      }),
  );
  document.querySelectorAll("[data-assign]").forEach(
    (e) =>
      (e.onchange = async () => {
        if (!e.value) return;
        try {
          const i = allIssues().find((x) => x.id === e.dataset.assign);
          await command("issue.claim", {
            id: i.id,
            assignee: e.value,
            version: i.version,
          });
        } catch (e) {
          toast(e.message);
        }
      }),
  );
  bindForm("#quick-step", async (f) => {
    if (!advanceWarning()) return false;
    await command("step.publish", dataFromStep(f));
  });
  bindForm("#plan-form", async (f) => {
    await command(editing ? "step.edit" : "step.add", {
      ...dataFromStep(f),
      ...(editing ? { id: editing } : {}),
    });
    editing = "";
    render();
  });
  bindForm("#issue-form", async (f) =>
    command("issue.add", { step: current().id, title: f.get("title") }),
  );
  bindForm("#seat-form", async (f) =>
    command("person.seat", { seat: f.get("seat") }),
  );
  bindForm("#settings-form", async (f) =>
    command("room.settings", {
      expected: Number(f.get("expected")),
      announcement: f.get("announcement"),
    }),
  );
  bindForm("#helper-form", async (f) => {
    const r = await command("role.create", {
      role: "helper",
      name: f.get("name"),
    });
    showShare(
      "助教邀请",
      location.origin +
        `/?room=${code}&role=helper#access=${encodeURIComponent(r.token)}`,
    );
  });
  if ($("#template-file"))
    $("#template-file").onchange = async (e) => {
      try {
        const file = e.target.files[0];
        if (!file) return;
        if (file.size > 100000) throw Error("模板不能超过 100 KB");
        await command("template.import", {
          template: JSON.parse(await file.text()),
        });
        toast("模板已加入待发布步骤");
      } catch (e) {
        toast(e.message);
      } finally {
        e.target.value = "";
      }
    };
}
async function actBase(action, id) {
  const i = allIssues().find((x) => x.id === id),
    s = current();
  switch (action) {
    case "state":
      if (!connected) throw Error("等待同步后再反馈");
      await command("status", { step: s.id, state: id, version: s.version });
      toast("状态已同步");
      break;
    case "catchup":
      await command("catchup", { step: id });
      break;
    case "save-tip": {
      const source = allIssues().find((x) =>
        x.answers.some((a) => a.id === id),
      );
      const answer = source.answers.find((a) => a.id === id),
        step = state.steps.find((x) => x.id === source.step);
      await command("step.notes", {
        id: step.id,
        tips: [step.tips, source.title + "：" + answer.body, answer.link]
          .filter(Boolean)
          .join("\n"),
      });
      toast("已加入步骤经验，导出模板或复制场次时带走");
      break;
    }
    case "reason":
      await command("issue.add", { step: s.id, title: s.reasons[Number(id)] });
      break;
    case "agree":
      await command("issue.agree", { id });
      break;
    case "recover":
    case "still":
      await command("issue." + action, { id, version: i.member_version });
      toast(
        action === "recover"
          ? "已记录恢复；完成当前操作后请再点「完成」"
          : "已重新标记受阻",
      );
      break;
    case "withdraw":
      await command("issue.withdraw", { id, version: i.member_version });
      break;
    case "claim":
    case "unclaim":
      await command("issue.claim", {
        id,
        version: i.version,
        assignee: action === "unclaim" ? "" : state.me.id,
      });
      break;
    case "issue-close":
      await command("issue.close", { id });
      break;
    case "answer":
      openAnswer(i);
      break;
    case "merge":
      openMerge(i);
      break;
    case "start-step":
      if (advanceWarning()) {
        historyId = "";
        await command("step.start", { id });
        page = "live";
        render();
      }
      break;
    case "edit-step":
      editing = id;
      render(false);
      break;
    case "cancel-edit":
      editing = "";
      render(false);
      break;
    case "delete-step":
      if (confirm("删除这个尚未发布的步骤？"))
        await command("step.delete", { id });
      break;
    case "move-up":
    case "move-down": {
      const ids = state.steps
          .filter((x) => x.phase === "draft")
          .map((x) => x.id),
        from = ids.indexOf(id),
        to = from + (action === "move-up" ? -1 : 1);
      if (to >= 0 && to < ids.length) {
        [ids[from], ids[to]] = [ids[to], ids[from]];
        await command("step.reorder", { ids });
      }
      break;
    }
    case "template-export":
      download(
        code + "-步骤模板.json",
        await request("/" + code + "/template", undefined, "GET"),
      );
      break;
    case "copy-room": {
      const r = await command("room.copy", {});
      localStorage.setItem(`${r.code}:host`, r.token);
      location.href = `/?room=${r.code}&host=1`;
      break;
    }
    case "invite":
      showShare("参与者加入链接", location.origin + "/?room=" + code);
      break;
    case "new-screen": {
      const r = await command("role.create", {
        role: "screen",
        name: "投屏 " + new Date().toLocaleTimeString(),
      });
      showShare(
        "只读投屏链接",
        location.origin +
          `/?room=${code}&role=screen#access=${encodeURIComponent(r.token)}`,
      );
      break;
    }
    case "revoke":
      if (confirm("撤销后该身份立即失效，确定？"))
        await command("role.revoke", { id });
      break;
    case "timer":
      await command("room.timer", { seconds: Number(id) });
      break;
    case "notifications":
      if (!("Notification" in window)) throw Error("当前浏览器不支持系统通知");
      if ((await Notification.requestPermission()) === "granted") {
        localStorage.setItem(code + ":notify", "yes");
        toast("提醒已开启；关闭网页后不会后台通知");
      } else toast("未开启提醒，页内同步仍可用");
      break;
    case "save-credential":
      download(code + "-" + state.role + "-恢复凭证.json", {
        room: code,
        role: state.role,
        token,
        notice: "请私下保管，在首页身份恢复入口使用",
      });
      break;
    case "remove":
      if (confirm("移出有效人数？原记录和历史快照保留。"))
        await command("person.remove", { id });
      break;
    case "leave":
      if (confirm("离开后当前身份失效，再加入将创建新身份。确定？")) {
        await command("person.leave", {});
        localStorage.removeItem(`${code}:${role}`);
        location.href = "/";
      }
      break;
    case "close":
      if (confirm("结束后停止反馈，记录保留。确定结束？"))
        await command("room.close", {});
      break;
    case "delete-room":
      if (confirm("永久删除房间及全部记录？请先导出。")) {
        await command("room.delete", {});
        location.href = "/";
      }
      break;
    case "csv":
    case "json": {
      const ctl = new AbortController(),
        timer = setTimeout(() => ctl.abort(), 12000);
      try {
        const r = await fetch(`/api/rooms/${code}/export?format=${action}`, {
          headers: { Authorization: token },
          signal: ctl.signal,
        });
        if (!r.ok) throw Error("导出失败");
        download(
          code + "-复盘." + action,
          await r.text(),
          action === "csv" ? "text/csv" : "application/json",
        );
      } finally {
        clearTimeout(timer);
      }
      break;
    }
    case "print":
      window.print();
      break;
  }
}
function openAnswer(i) {
  $("#edit-dialog")?.remove();
  const d = document.createElement("dialog");
  d.id = "edit-dialog";
  d.innerHTML = `<h2>发布处理办法</h2><p>${esc(i.title)}</p><form id="answer-form"><label for="answer-body">处理说明</label><textarea id="answer-body" name="body" required maxlength="2000" placeholder="说明操作及成功标准"></textarea><label for="answer-link">帮助链接（可选）</label><input id="answer-link" name="link" type="url" maxlength="1000"><label for="answer-environment">适用环境（未选择表示通用）</label>${platformSelect("answer-environment","environment")}<label class="checkbox"><input type="checkbox" name="public"> 同时公开到匿名投屏（请确认不含个人信息）</label><button type="submit">发布给参与者</button></form>${button("取消", "dismiss")}`;
  document.body.append(d);
  d.showModal();
  $("[data-action=dismiss]", d).onclick = () => d.close();
  bindForm("#answer-form", async (f) => {
    await command("issue.answer", {
      id: i.id,
      body: f.get("body"),
      environment:f.get("environment")||"unspecified",
      link: f.get("link"),
      public: f.get("public") === "on",
    });
    d.close();
    toast("已发布，等待参与者确认恢复");
  });
}
function openMerge(i) {
  const targets = allIssues().filter((x) => x.step === i.step && x.id !== i.id);
  if (!targets.length) {
    toast("同一步骤没有其他可合并卡点");
    return;
  }
  $("#edit-dialog")?.remove();
  const d = document.createElement("dialog");
  d.id = "edit-dialog";
  d.innerHTML = `<h2>合并同类卡点</h2><p>将「${esc(i.title)}」合并，成员去重，原题目留痕。</p><form id="merge-form"><label for="merge-target">保留的卡点</label><select id="merge-target" name="target">${targets.map((x) => `<option value="${x.id}">${esc(x.title)}</option>`).join("")}</select><button type="submit">确认合并</button></form>${button("取消", "dismiss")}`;
  document.body.append(d);
  d.showModal();
  $("[data-action=dismiss]", d).onclick = () => d.close();
  bindForm("#merge-form", async (f) => {
    await command("issue.merge", { id: i.id, target: f.get("target") });
    d.close();
  });
}
function notifyUpdate(next) {
  const active = next.steps.find((x) => x.id === next.current),
    message =
      next.role === "person"
        ? next.steps
            .flatMap((s) => s.issues)
            .filter((i) => i.mine && i.my_active)
            .flatMap((i) => i.answers)
            .at(-1)?.id
        : active &&
          `${active.counts.stuck}:${active.total > 0 && active.counts.done === active.total}`;
  if (previousCurrent !== undefined && next.current !== previousCurrent) {
    historyId = "";
    toast("讲解者已更新当前检查点");
  }
  if (
    previousCurrent !== undefined &&
    message &&
    message !== previousAlert &&
    localStorage.getItem(code + ":notify") === "yes" &&
    "Notification" in window &&
    Notification.permission === "granted"
  ) {
    new Notification("跟上了吗", {
      body:
        next.role === "person"
          ? "你关注的卡点有处理办法，请查看。"
          : "本轮反馈有变化，请查看面板。",
      tag: "workshop-" + code,
    });
  }
  previousCurrent = next.current;
  previousAlert = message || "";
}
function cleanup() {
  generation++;
  streamController?.abort();
  clearInterval(heartbeatTimer);
}
async function start() {
  cleanup();
  const run = generation;
  try {
    await refresh();
  } catch (e) {
    app.innerHTML = `<div class="error">${esc(e.message)} <a href="/">返回首页或恢复身份</a></div>`;
    return;
  }
  if (state.role === "person") {
    const beat = () => request("/" + code + "/heartbeat", {}).catch(() => {});
    beat();
    heartbeatTimer = setInterval(beat, 15000);
  }
  let retries = 0;
  while (run === generation) {
    streamController = new AbortController();
    let watchdog;
    try {
      const r = await fetch(`/api/rooms/${code}/events`, {
        headers: { Authorization: token },
        signal: streamController.signal,
      });
      if (!r.ok) {
        if ([403, 404].includes(r.status)) {
          connection("身份已失效");
          toast("房间不存在或身份已撤销，请返回首页");
          return;
        }
        throw Error("连接失败");
      }
      const reader = r.body.getReader(),
        decoder = new TextDecoder();
      let buffer = "";
      const arm = () => {
        clearTimeout(watchdog);
        watchdog = setTimeout(() => streamController?.abort(), 20000);
      };
      arm();
      while (run === generation) {
        const { value, done } = await reader.read();
        if (done) break;
        arm();
        buffer += decoder.decode(value, { stream: true });
        let at;
        while ((at = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, at);
          buffer = buffer.slice(at + 2);
          if (frame.startsWith("event: gone")) {
            connection("房间或身份已失效");
            app.innerHTML =
              '<div class="error">房间已删除或身份已撤销。<a href="/">返回首页</a></div>';
            return;
          }
          if (frame.startsWith("data: ")) {
            const next = JSON.parse(frame.slice(6));
            notifyUpdate(next);
            state = next;
            connection("● 实时同步", true);
            const signature = `${next.revision}:${Math.floor(next.server_time / 10)}`;
            if (signature !== lastSignature) {
              lastSignature = signature;
              render();
            }
            retries = 0;
          }
        }
      }
      await reader.cancel().catch(() => {});
    } catch {
    } finally {
      clearTimeout(watchdog);
    }
    if (run !== generation) return;
    connection("连接中断，正在重连…");
    await new Promise((resolve) =>
      setTimeout(
        resolve,
        Math.min(15000, 1000 * 2 ** Math.min(retries++, 4)) +
          Math.random() * 500,
      ),
    );
  }
}
window.addEventListener("pagehide", cleanup);
window.addEventListener("pageshow", (e) => {
  if (e.persisted && token) start();
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && token) {
    connection("恢复页面，正在同步…");
    refresh().catch(() => connection("等待恢复连接…"));
  }
});
setInterval(() => {
  if ($("#countdown")) $("#countdown").textContent = timerText();
}, 1000);
if (!code) landing();
else if (token) start();
else if (role !== "person") {
  app.innerHTML =
    '<div class="error">本浏览器没有此身份凭证。请使用邀请链接，或在首页恢复身份。<a href="/">返回首页</a></div>';
} else join();
