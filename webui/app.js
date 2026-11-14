"use strict";

const $ = id => document.getElementById(id);
let csrf = "";
let currentStatus = null;
let browserDirectory = "";
let selectedFile = "";
let modalKind = "directory";

function humanSize(n) {
  if (n == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, value = Number(n);
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
  return value.toFixed(i ? 1 : 0) + " " + units[i];
}
function element(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = String(content);
  return node;
}
function clear(node) { node.replaceChildren(); }
function notice(message, ok = false) {
  const node = $("notice");
  node.hidden = !message;
  node.textContent = message || "";
  node.className = ok ? "notice ok" : "notice";
  if (message) window.scrollTo({top: 0, behavior: "smooth"});
}
async function get(url) {
  const response = await fetch(url, {cache: "no-store"});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "请求失败");
  return payload;
}
async function post(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-GU-CSRF": csrf},
    body: JSON.stringify(payload)
  });
  const output = await response.json();
  if (!response.ok) throw new Error(output.error || "操作失败");
  return output;
}
async function action(url, payload, message = "") {
  try {
    await post(url, payload);
    if (message) notice(message, true);
  } catch (error) {
    notice(error.message);
  }
  await refresh();
}
const statusText = {
  queued: "等待上传",
  uploading: "正在上传",
  api_accepted: "API 已接收",
  duplicate: "已去重",
  retrying: "等待重试",
  failed: "上传失败",
  paused: "来源已暂停"
};
function renderCounts(counts) {
  const sum = key => counts[key]?.count || 0;
  const total = Object.values(counts).reduce((n, v) => n + v.count, 0);
  $("stat-total").textContent = total;
  $("stat-queued").textContent = sum("queued") + sum("paused");
  $("stat-accepted").textContent = sum("api_accepted");
  $("stat-failed").textContent = sum("failed") + sum("retrying");
  const processed = sum("api_accepted") + sum("duplicate");
  const pct = total ? Math.round(processed * 100 / total) : 0;
  $("task-percent").textContent = pct + "%";
  $("task-bar").style.width = pct + "%";
}
function renderSources(sources) {
  const target = $("sources-list");
  clear(target);
  if (!sources.length) {
    target.appendChild(element("div", "empty", "尚无来源。添加文件夹或单个文件后会只读扫描并入队。"));
    return;
  }
  for (const source of sources) {
    const row = element("div", "source-entry");
    row.appendChild(element("span", "source-ic", source.kind === "directory" ? "▣" : "▤"));
    const info = element("div", "source-meta");
    info.appendChild(element("strong", null, source.path.split("/").pop() || source.path));
    info.appendChild(element("small", null, source.path));
    row.appendChild(info);
    const checkbox = element("input");
    checkbox.type = "checkbox";
    checkbox.checked = !!source.enabled;
    checkbox.title = source.enabled ? "暂停来源" : "恢复来源";
    checkbox.addEventListener("change", () =>
      action("/api/source/toggle", {id: source.id, enabled: checkbox.checked},
        checkbox.checked ? "来源已恢复" : "来源已暂停，已登记任务保留"));
    const toggle = element("label", "switch");
    toggle.append(checkbox, element("span"));
    row.appendChild(toggle);
    const remove = element("button", "btn warn", "移除");
    remove.addEventListener("click", () => {
      if (confirm("只移除监控记录，不删除真实文件。继续吗？")) {
        action("/api/source/remove", {id: source.id}, "已移除来源，照片不会被删除");
      }
    });
    row.appendChild(remove);
    target.appendChild(row);
  }
}
function renderJobs(jobs) {
  const body = $("jobs-body");
  clear(body);
  if (!jobs.length) {
    const tr = element("tr");
    const td = element("td", "empty", "暂无任务。添加一个文件或文件夹后会自动扫描。");
    td.colSpan = 5;
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const job of jobs) {
    const tr = element("tr");
    const nameCell = element("td");
    nameCell.appendChild(element("div", "filename", job.name));
    nameCell.appendChild(element("div", "path", job.path));
    tr.appendChild(nameCell);
    tr.appendChild(element("td", null, humanSize(job.bytes)));
    const statusCell = element("td");
    statusCell.appendChild(element("span", "status " + job.status, statusText[job.status] || job.status));
    if (job.error) {
      const hint = element("div", "path", job.error);
      hint.title = job.error;
      statusCell.appendChild(hint);
    }
    tr.appendChild(statusCell);
    const progress = {
      queued: "尚未传输",
      uploading: "上传中 · 实际字节比例未知",
      api_accepted: "API 接收 · 云端待核验",
      duplicate: "内容相同 · 跳过",
      retrying: "等待下一次重试",
      failed: "失败，未完成",
      paused: "已暂停"
    };
    tr.appendChild(element("td", "muted", progress[job.status] || "—"));
    const actions = element("td");
    if (["failed", "retrying"].includes(job.status)) {
      const btn = element("button", "btn soft", "重新入队");
      btn.addEventListener("click", () => action("/api/retry", {id: job.id}, "已加入重试队列"));
      actions.appendChild(btn);
    } else {
      actions.appendChild(element("span", "muted", "—"));
    }
    tr.appendChild(actions);
    body.appendChild(tr);
  }
}
async function refresh() {
  try {
    const [status, tasks] = await Promise.all([get("/api/status"), get("/api/jobs")]);
    csrf = status.csrf;
    currentStatus = status;
    renderCounts(status.counts);
    renderSources(status.sources);
    renderJobs(tasks.jobs);
    $("monitor-switch").checked = status.monitor_enabled;
    $("upload-switch").checked = status.upload_enabled;
    $("credential-state").textContent = status.credentials_present ? "存在本机凭据（有效性未验证）" : "尚未配置独立账号";
    $("upload-reason").textContent = status.allow_real_upload
      ? (status.credentials_present ? "已设置独立凭据，可手动启动或停止" : "缺少独立凭据，无法启动上传")
      : "安全锁定中：需在本地启动参数显式启用上传";
    $("scan-label").textContent = status.scan_running ? "正在扫描" : (status.monitor_enabled ? "监控已启用" : "监控已关闭");
    $("last-scan").textContent = status.last_scan
      ? "最近扫描：" + status.last_scan + (status.last_scan_error ? " · " + status.last_scan_error : "")
      : "尚未扫描";
  } catch (error) {
    notice("本地 WebUI 服务暂不可达：" + error.message);
  }
}
async function browse(path) {
  try {
    const data = await get("/api/browse" + (path ? "?path=" + encodeURIComponent(path) : ""));
    browserDirectory = data.path;
    selectedFile = "";
    $("browser-path").value = data.path;
    const target = $("browser-items");
    clear(target);
    for (const folder of data.folders) {
      const row = element("div", "browser-row");
      row.appendChild(element("span", null, "▣  " + folder.name));
      row.appendChild(element("small", null, "打开 ›"));
      row.addEventListener("click", () => browse(folder.path));
      target.appendChild(row);
    }
    for (const file of data.files) {
      const row = element("div", "browser-row");
      row.appendChild(element("span", null, "▤  " + file.name));
      row.appendChild(element("small", null, humanSize(file.bytes)));
      row.addEventListener("click", () => {
        if (modalKind === "directory") {
          notice("请选择一个文件夹，或将类型改为“单个文件”");
          return;
        }
        selectedFile = file.path;
        $("browser-path").value = file.path;
        target.querySelectorAll(".selected").forEach(node => node.classList.remove("selected"));
        row.classList.add("selected");
      });
      target.appendChild(row);
    }
    if (!target.children.length) target.appendChild(element("div", "empty", "此目录没有可用的子文件夹或媒体文件"));
    $("up-btn").disabled = !data.parent;
    $("up-btn").onclick = () => browse(data.parent);
  } catch (error) {
    $("browser-items").replaceChildren(element("div", "empty", error.message));
  }
}
function openDialog() {
  modalKind = "directory";
  document.querySelector('input[name="kind"][value="directory"]').checked = true;
  $("source-dialog").showModal();
  browse();
}
async function addSource() {
  const type = document.querySelector('input[name="kind"]:checked').value;
  const path = $("browser-path").value.trim();
  try {
    await post("/api/sources", {kind: type, path});
    $("source-dialog").close();
    notice("已添加来源。只会扫描和登记，真实上传仍受独立开关控制。", true);
    await refresh();
  } catch (error) {
    $("browser-items").replaceChildren(element("div", "empty", error.message));
  }
}
document.addEventListener("DOMContentLoaded", () => {
  $("add-btn").addEventListener("click", openDialog);
  $("close-dialog").addEventListener("click", () => $("source-dialog").close());
  $("cancel-btn").addEventListener("click", () => $("source-dialog").close());
  $("confirm-btn").addEventListener("click", addSource);
  $("browse-btn").addEventListener("click", () => browse($("browser-path").value));
  $("scan-btn").addEventListener("click", () => action("/api/scan", {}, "已请求重新扫描"));
  $("refresh-btn").addEventListener("click", refresh);
  $("monitor-switch").addEventListener("change", e =>
    action("/api/monitor", {enabled: e.target.checked},
      e.target.checked ? "目录监控已启动（不代表上传已开启）" : "目录监控已关闭"));
  $("upload-switch").addEventListener("change", e =>
    action("/api/upload", {enabled: e.target.checked},
      e.target.checked ? "已启用独立账号上传" : "真实上传已停止"));
  for (const button of document.querySelectorAll('input[name="kind"]')) {
    button.addEventListener("change", e => {
      modalKind = e.target.value;
      $("browser-path").value = browserDirectory;
      selectedFile = "";
      browse(browserDirectory);
    });
  }
  refresh();
  setInterval(refresh, 4000);
});
