"use strict";

const $ = id => document.getElementById(id);
let csrf = "";
let currentStatus = null;
let browserDirectory = "";
let selectedFile = "";
let modalKind = "directory";
let selectedSourceId = "";
let showHiddenRecords = false;
const expandedEventSources = new Set();
let pageOffset = 0;
const pageSize = 100;

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

function collapseHiddenSourceDetails(sourceId) {
  // Removing/hiding a source must also clear previously expanded DOM tables.
  // Otherwise its rows would remain visible until the user reloads the page.
  if (selectedSourceId === String(sourceId)) {
    selectedSourceId = "";
    $("index-details").open = false;
    $("index-summary").textContent = "目录明细已收起。选择可见来源后按需展开。";
    $("folders-list").replaceChildren();
  }
  $("jobs-details").open = false;
  $("jobs-body").replaceChildren();
}
const statusText = {
  queued: "等待上传", uploading: "正在上传", api_accepted: "API 已接收",
  duplicate: "已去重", retrying: "等待重试", failed: "上传失败",
  interrupted: "中断待人工核对", paused: "来源已暂停"
};

function renderCounts(counts) {
  const sum = key => counts[key]?.count || 0;
  const total = Object.values(counts).reduce((n, v) => n + v.count, 0);
  $("stat-total").textContent = total;
  $("stat-queued").textContent = sum("queued") + sum("paused");
  $("stat-accepted").textContent = sum("api_accepted");
  $("stat-failed").textContent = sum("failed") + sum("retrying") + sum("interrupted");
  const processed = sum("api_accepted") + sum("duplicate");
  const pct = total ? Math.round(processed * 100 / total) : 0;
  $("task-percent").textContent = pct + "%";
  $("task-bar").style.width = pct + "%";
}

function renderSources(sources) {
  const target = $("sources-list");
  clear(target);
  if (!sources.length) {
    target.appendChild(element("div", "empty", "尚无来源。添加文件夹或单个文件后自动扫描并保留历史索引。"));
    return;
  }
  for (const source of sources) {
    const row = element("div", "source-entry" + (source.archived ? " archived" : ""));
    row.appendChild(element("span", "source-ic", source.kind === "directory" ? "▣" : "▤"));
    const info = element("div", "source-meta");
    const sourceName = element("strong", null, source.path.split("/").pop() || source.path);
    sourceName.title = source.path;
    info.appendChild(sourceName);
    const badges = [source.location_kind === "network" ? "远程共享" : "本地 / 映射盘"];
    if (source.ui_visibility === "removed") badges.push("已从界面移除 · 历史保留");
    else if (source.ui_visibility === "hidden") badges.push("已隐藏");
    else if (source.archived) badges.push("已归档");
    else if (source.availability === "offline") badges.push("离线 · 历史保留");
    else if (source.availability === "interrupted") badges.push("扫描中断");
    else if (source.availability === "unknown") badges.push("等待重新扫描");
    else if (!source.enabled) badges.push("已暂停");
    const sourcePath = element("small", null, source.path);
    sourcePath.title = source.path; // truncated long paths remain inspectable.
    info.appendChild(sourcePath);
    info.appendChild(element("small", "source-badges", badges.join(" · ")));
    info.appendChild(element("small", "source-badges",
      "已索引 " + source.indexed_files + " 项（图片 " + source.images + " / 视频 " + source.videos +
      "）· API 已接收 " + source.api_accepted + " · 云端人工核验 " + source.cloud_verified));
    row.appendChild(info);
    // Keep every action inside ONE grid cell. Previously they were siblings of
    // the text column and a full-width <details>; flexbox compressed the path
    // into a ~10px column, rendering Chinese and Latin text vertically.
    const controls = element("div", "source-controls");
    const index = element("button", "btn soft", "查看索引");
    index.addEventListener("click", () => {
      $("job-source-filter").value = String(source.id);
      selectedSourceId = String(source.id);
      pageOffset = 0;
      $("index-details").open = true;
      showFolders(source);
      refresh();
      $("index").scrollIntoView({behavior: "smooth", block: "start"});
    });
    controls.appendChild(index);
    if (source.ui_visibility !== "visible") {
      const show = element("button", "btn soft", "显示");
      show.addEventListener("click", () =>
        action("/api/source/display", {id: source.id, mode: "visible"},
          "已恢复界面显示，历史索引及备份状态没有改变"));
      controls.appendChild(show);
    } else if (source.archived) {
      const restore = element("button", "btn primary", "恢复历史");
      restore.addEventListener("click", () =>
        action("/api/source/restore", {id: source.id},
          "历史来源已恢复。旧文件备份记录仍然保留，不会重新登记已接受的内容。"));
      controls.appendChild(restore);
    } else {
      const checkbox = element("input");
      checkbox.type = "checkbox";
      checkbox.checked = !!source.enabled;
      checkbox.title = source.enabled ? "暂停来源" : "恢复来源";
      checkbox.addEventListener("change", () =>
        action("/api/source/toggle", {id: source.id, enabled: checkbox.checked},
          checkbox.checked ? "来源已恢复" : "来源已暂停，索引和记录保留"));
      const toggle = element("label", "switch");
      toggle.append(checkbox, element("span"));
      controls.appendChild(toggle);
    }
    if (source.ui_visibility === "visible") {
      const hide = element("button", "btn soft", "隐藏");
      hide.title = "只隐藏页面显示，不改变监控和 SQLite 任务状态";
      hide.addEventListener("click", () => {
        collapseHiddenSourceDetails(source.id);
        action("/api/source/display", {id: source.id, mode: "hidden"},
          "索引已隐藏，数据库和备份历史完整保留");
      });
      controls.appendChild(hide);
      const remove = element("button", "btn warn", "删除显示");
      remove.title = "移除本页映射并停止来源监控；不删除数据库及照片";
      remove.addEventListener("click", () => {
        if (confirm("只从 WebUI 删除显示并停止这条来源映射；不删除 SQLite 历史、上传进度或任何照片。确定吗？")) {
          collapseHiddenSourceDetails(source.id);
          action("/api/source/display", {id: source.id, mode: "removed"},
            "已从界面移除。SQLite 历史仍然保留，重新添加相同目录即可恢复");
        }
      });
      controls.appendChild(remove);
    }
    row.appendChild(controls);
    const events = element("details", "source-event-details");
    const eventHeader = element("summary", null, "操作记录");
    events.appendChild(eventHeader);
    events.addEventListener("toggle", async () => {
      if (events.open) expandedEventSources.add(source.id);
      else expandedEventSources.delete(source.id);
      if (!events.open || events.dataset.loaded) return;
      try {
        const data = await get("/api/source/events?source_id=" + source.id);
        for (const entry of data.events) {
          events.appendChild(element("div", "event-line",
            entry.created_at + " · " + entry.action + " · " + entry.note));
        }
        if (!data.events.length) events.appendChild(element("div", "event-line", "尚无操作历史"));
        events.dataset.loaded = "1";
      } catch (error) { events.appendChild(element("div", "event-line", error.message)); }
    });
    if (expandedEventSources.has(source.id)) events.open = true;
    row.appendChild(events);
    target.appendChild(row);
  }
}

async function showFolders(source) {
  const target = $("folders-list");
  const summary = $("index-summary");
  summary.textContent = source.path + " · " + source.indexed_folders + " 个目录 · " +
    source.indexed_files + " 个媒体 · 云端人工核验 " + source.cloud_verified;
  if (!$("index-details").open) return;
  target.replaceChildren(element("div", "empty", "读取 SQLite 目录索引中…"));
  try {
    const result = await get("/api/folders?source_id=" + encodeURIComponent(source.id)
      + (showHiddenRecords ? "&include_hidden=1" : ""));
    clear(target);
    if (!result.folders.length) {
      target.appendChild(element("div", "empty", "此来源尚未建立目录索引。重新连接硬盘后执行扫描即可。"));
      return;
    }
    const table = element("table", "folder-table");
    const head = element("thead");
    const tr = element("tr");
    for (const col of ["目录路径", "媒体", "照片", "视频", "API 已接收", "人工云端核验", "离线 / 丢失"]) {
      tr.appendChild(element("th", null, col));
    }
    head.appendChild(tr);
    table.appendChild(head);
    const body = element("tbody");
    for (const folder of result.folders) {
      const row = element("tr");
      for (const data of [folder.path, folder.file_count, folder.image_count,
                          folder.video_count, folder.accepted_count, folder.verified_count,
                          folder.missing_count]) {
        row.appendChild(element("td", null, data));
      }
      body.appendChild(row);
    }
    table.appendChild(body);
    target.appendChild(table);
  } catch (error) {
    target.replaceChildren(element("div", "empty", error.message));
  }
}

function renderJobFilters(sources) {
  const select = $("job-source-filter");
  const previous = select.value;
  clear(select);
  const first = element("option", null, "全部来源（包括历史）");
  first.value = "";
  select.appendChild(first);
  for (const source of sources) {
    const option = element("option", null,
      (source.archived ? "[归档] " : "") + source.path.split("/").pop() + " · " + source.id);
    option.value = String(source.id);
    select.appendChild(option);
  }
  select.value = sources.some(s => String(s.id) === previous) ? previous : "";
}

function renderJobs(jobs) {
  const body = $("jobs-body");
  clear(body);
  if (!jobs.length) {
    const tr = element("tr");
    const td = element("td", "empty", "暂无符合条件的历史任务。文件夹或网络盘重新挂载后可扫描。");
    td.colSpan = 7;
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const job of jobs) {
    const tr = element("tr");
    const nameCell = element("td");
    nameCell.appendChild(element("div", "filename", job.name));
    nameCell.appendChild(element("div", "path", job.path));
    if (!job.file_available) nameCell.appendChild(element("div", "path", "当前文件不可访问 · 历史保留"));
    tr.appendChild(nameCell);
    tr.appendChild(element("td", null,
      (job.media_kind === "video" ? "视频" : job.media_kind === "image" ? "图片" : "媒体")
      + " · " + (job.extension || "未知").toUpperCase()));
    tr.appendChild(element("td", null, humanSize(job.bytes)));
    const statusCell = element("td");
    statusCell.appendChild(element("span", "status " + job.status,
      statusText[job.status] || job.status));
    if (job.error) {
      const hint = element("div", "path", job.error);
      hint.title = job.error;
      statusCell.appendChild(hint);
    }
    tr.appendChild(statusCell);
    const verifiedCell = element("td");
    if (job.cloud_verified_at) {
      verifiedCell.appendChild(element("span", "status api_accepted", "人工已核验"));
      verifiedCell.appendChild(element("div", "path", job.cloud_verified_at));
    } else {
      verifiedCell.appendChild(element("span", "muted", "未核验"));
    }
    tr.appendChild(verifiedCell);
    const progress = {
      queued: "尚未传输", uploading: "上传中 · 字节比例未知",
      api_accepted: "API 接收 · 未自动云端核验",
      duplicate: "内容相同 · 跳过", retrying: "等待下一次重试",
      failed: "失败，未完成", interrupted: "结果未知 · 需先核对云端", paused: "已暂停"
    };
    tr.appendChild(element("td", "muted", progress[job.status] || "—"));
    const actions = element("td");
    if (["failed", "retrying", "interrupted"].includes(job.status)) {
      const retry = element("button", "btn soft", "重新入队");
      retry.addEventListener("click", () => {
        if (job.status !== "interrupted" || confirm("这项任务在上传中断时云端结果未知，请先检查 Google Photos 是否已经存在，再决定是否重试。确定重新入队？")) {
          action("/api/retry", {id: job.id}, "已重新入队");
        }
      });
      actions.appendChild(retry);
    } else if (job.status === "api_accepted") {
      const verify = element("button", "btn soft", job.cloud_verified_at ? "取消核验" : "人工确认");
      verify.addEventListener("click", () => {
        const next = !job.cloud_verified_at;
        if (!next || confirm("请先在 Google Photos 官方页面核对这项媒体确实存在。此操作只记录人工核验，不代表 Google 官方 API 自动验证。是否已核实？")) {
          action("/api/cloud/verify", {id: job.id, verified: next},
            next ? "已记录人工核验时间" : "已取消人工核验标记");
        }
      });
      actions.appendChild(verify);
    } else {
      actions.appendChild(element("span", "muted", "—"));
    }
    tr.appendChild(actions);
    body.appendChild(tr);
  }
}

function renderPagination(result) {
  const total = result.total || 0;
  const first = total ? result.offset + 1 : 0;
  const last = Math.min(total, result.offset + result.jobs.length);
  $("page-label").textContent = first + "–" + last + " / " + total + " 条";
  $("page-prev").disabled = result.offset <= 0;
  $("page-next").disabled = last >= total;
}

async function refresh() {
  try {
    const status = await get("/api/status" + (showHiddenRecords ? "?include_hidden=1" : ""));
    csrf = status.csrf;
    currentStatus = status;
    renderCounts(status.counts);
    renderSources(status.sources);
    renderJobFilters(status.sources);
    $("show-hidden-btn").textContent = (showHiddenRecords ? "隐藏历史记录" : "显示隐藏记录")
      + "（" + status.hidden_sources + "）";
    $("show-hidden-btn").setAttribute("aria-pressed", showHiddenRecords ? "true" : "false");
    $("stored-history-label").textContent = " SQLite 全库仍保存 " + status.stored_files + " 项媒体历史。";
    if ($("jobs-details").open) {
      const params = new URLSearchParams();
      if ($("job-source-filter").value) params.set("source_id", $("job-source-filter").value);
      if ($("job-kind-filter").value) params.set("kind", $("job-kind-filter").value);
      if ($("job-status-filter").value) params.set("status", $("job-status-filter").value);
      if (showHiddenRecords) params.set("include_hidden", "1");
      params.set("limit", String(pageSize));
      params.set("offset", String(pageOffset));
      const tasks = await get("/api/jobs?" + params.toString());
      renderJobs(tasks.jobs);
      renderPagination(tasks);
    }
    $("monitor-switch").checked = status.monitor_enabled;
    $("upload-switch").checked = status.upload_enabled;
    $("credential-state").textContent = status.credentials_present
      ? "存在本机凭据（有效性未验证）" : "尚未配置独立账号";
    $("upload-reason").textContent = status.allow_real_upload
      ? (status.credentials_present ? "独立凭据存在，可手动启动或停止" : "缺少独立凭据，无法上传")
      : "安全锁定：需在本机启动参数显式启用上传";
    $("scan-label").textContent = status.scan_running
      ? "正在扫描" : (status.monitor_enabled ? "监控已启用" : "监控已关闭");
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
          notice("请选择文件夹，或将类型切换为“单个文件”");
          return;
        }
        selectedFile = file.path;
        $("browser-path").value = file.path;
        target.querySelectorAll(".selected").forEach(node => node.classList.remove("selected"));
        row.classList.add("selected");
      });
      target.appendChild(row);
    }
    if (!target.children.length) target.appendChild(element("div", "empty", "没有可用的子文件夹或媒体文件"));
    $("up-btn").disabled = !data.parent;
    $("up-btn").onclick = () => browse(data.parent);
  } catch (error) {
    $("browser-items").replaceChildren(element("div", "empty", error.message));
  }
}

async function refreshVolumes() {
  const root = $("network-mounts");
  root.replaceChildren(element("div", "empty", "读取 macOS 已挂载网络共享…"));
  try {
    const volumes = await get("/api/volumes");
    clear(root);
    if (!volumes.network.length) {
      root.appendChild(element("div", "empty",
        "当前没有已挂载 SMB/NFS/WebDAV 网络共享。先输入 smb:// 地址，在 Finder 授权后刷新。"));
    }
    for (const volume of volumes.network) {
      const button = element("button", "browser-row network-choice");
      button.type = "button";
      button.appendChild(element("span", null, "▣ " + volume.name + " · " + volume.fs_type.toUpperCase()));
      button.appendChild(element("small", null, "选择网络盘 ›"));
      button.addEventListener("click", () => browse(volume.path));
      root.appendChild(button);
    }
  } catch (error) {
    root.replaceChildren(element("div", "empty", error.message));
  }
}

async function connectShare() {
  const url = $("share-url").value.trim();
  if (!url.startsWith("smb://")) {
    notice("请输入 smb://服务器/共享文件夹；不要在地址中附加账号或密码");
    return;
  }
  try {
    const result = await post("/api/share/connect", {url});
    notice(result.message, true);
    await refreshVolumes();
  } catch (error) {
    notice(error.message);
  }
}

function selectStorage(mode) {
  const network = mode === "network";
  $("storage-local").classList.toggle("selected", !network);
  $("storage-network").classList.toggle("selected", network);
  $("network-mount-panel").hidden = !network;
  if (network) refreshVolumes();
}

function openDialog() {
  modalKind = "directory";
  document.querySelector('input[name="kind"][value="directory"]').checked = true;
  selectStorage("local");
  $("source-dialog").showModal();
  browse();
}

async function addSource() {
  const type = document.querySelector('input[name="kind"]:checked').value;
  const path = $("browser-path").value.trim();
  try {
    await post("/api/sources", {kind: type, path});
    $("source-dialog").close();
    notice("已添加 / 恢复来源。已索引的任务进度会保留，不会自动开始真实云端上传。", true);
    await refresh();
  } catch (error) {
    $("browser-items").replaceChildren(element("div", "empty", error.message));
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("add-btn").addEventListener("click", openDialog);
  $("show-hidden-btn").addEventListener("click", () => {
    showHiddenRecords = !showHiddenRecords;
    pageOffset = 0;
    if (!showHiddenRecords) {
      $("index-details").open = false;
      selectedSourceId = "";
      $("index-summary").textContent = "目录明细已收起。选择来源并点击“查看索引”可展开。";
    }
    refresh();
  });
  $("index-details").addEventListener("toggle", () => {
    if ($("index-details").open && currentStatus?.sources?.length) {
      const source = currentStatus.sources.find(s => String(s.id) === selectedSourceId)
        || currentStatus.sources[0];
      selectedSourceId = String(source.id);
      showFolders(source);
    }
  });
  $("jobs-details").addEventListener("toggle", () => {
    if ($("jobs-details").open) refresh();
  });
  $("close-dialog").addEventListener("click", () => $("source-dialog").close());
  $("cancel-btn").addEventListener("click", () => $("source-dialog").close());
  $("confirm-btn").addEventListener("click", addSource);
  $("browse-btn").addEventListener("click", () => browse($("browser-path").value));
  $("storage-local").addEventListener("click", () => selectStorage("local"));
  $("storage-network").addEventListener("click", () => selectStorage("network"));
  $("refresh-volumes").addEventListener("click", refreshVolumes);
  $("connect-share-btn").addEventListener("click", connectShare);
  $("scan-btn").addEventListener("click", () => action("/api/scan", {}, "已请求重新扫描"));
  $("refresh-btn").addEventListener("click", refresh);
  $("page-prev").addEventListener("click", () => {
    pageOffset = Math.max(0, pageOffset - pageSize);
    refresh();
  });
  $("page-next").addEventListener("click", () => {
    pageOffset += pageSize;
    refresh();
  });
  for (const select of ["job-source-filter", "job-kind-filter", "job-status-filter"]) {
    $(select).addEventListener("change", () => { pageOffset = 0; refresh(); });
  }
  $("monitor-switch").addEventListener("change", e =>
    action("/api/monitor", {enabled: e.target.checked},
      e.target.checked ? "目录监控已启动（并非真实上传）" : "目录监控已关闭"));
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
