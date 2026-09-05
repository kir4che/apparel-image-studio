"use strict";
const $ = (selector) => document.querySelector(selector);
const node = (tag, text, cls) => {
  const el = document.createElement(tag);
  if (text != null) el.textContent = text;
  if (cls) el.className = cls;
  return el;
};
let state,
  token,
  selected = [],
  draft = null,
  active = null,
  busy = false,
  aiBusy = false,
  exporting = false,
  deleteMode = false,
  photoMode = "pair",
  singleCropPhotoId = null,
  singleCropDraft = null,
  singleCropBeforeEdit = null,
  previewCropMode = false,
  previewBaseSize = null,
  previewCropBeforeEdit = null;
let previewTimer,
  previewSerial = 0,
  previewController = null,
  saveChain = Promise.resolve(),
  saveTimer = null,
  savePending = null,
  saveWaiters = [],
  saveInFlight = 0,
  exportBatch = null,
  aiResults = [],
  aiRunToken = 0,
  aiFilter = "all",
  aiController = null,
  aiRunId = null,
  aiLogFile = null;
const photo = (id) => state.photos.find((p) => p.id === id);
const photoBaseBox = (id) => {
  const p = photo(id);
  const saved = state.photo_crops?.[id];
  if (saved) return [...saved];
  const top = state.ai_crops?.[id] || 0;
  return [p.white[0], top, p.white[1], p.height];
};
const cropVersions = new Map();
const pairPreviewCache = new Map();
const pairPreviewRequests = new Map();
const pairPreviewKey = (group) =>
  JSON.stringify({ group, format: state.format });
function invalidatePairPreview(groupId) {
  pairPreviewCache.delete(groupId);
  const request = pairPreviewRequests.get(groupId);
  request?.controller.abort();
  pairPreviewRequests.delete(groupId);
}
function requestPairPreview(group) {
  if (!group || !state) return;
  const id = group.id,
    key = pairPreviewKey(group),
    cached = pairPreviewCache.get(id),
    pending = pairPreviewRequests.get(id);
  if (cached?.key === key || pending?.key === key) return;
  pending?.controller.abort();
  const controller = new AbortController();
  pairPreviewRequests.set(id, { key, controller });
  const requestGroup = JSON.parse(JSON.stringify(group));
  api(
    "/api/preview",
    { group: requestGroup, format: state.format },
    { signal: controller.signal },
  )
    .then((result) => {
      const currentGroup = state.groups.find((item) => item.id === id);
      if (!currentGroup || pairPreviewKey(currentGroup) !== key) return;
      pairPreviewCache.set(id, { key, image: result.image });
      renderTray();
    })
    .catch((error) => {
      if (error.name !== "AbortError") return;
    })
    .finally(() => {
      if (pairPreviewRequests.get(id)?.controller === controller)
        pairPreviewRequests.delete(id);
    });
}
const photoSrc = (id) => {
  const hasCrop = state.photo_crops?.[id];
  const hasAi = state.ai_crops?.[id];
  const view = hasCrop ? "?view=crop" : hasAi ? "?view=ai" : "";
  const ts = cropVersions.has(id) ? `${view ? "&" : "?"}t=${cropVersions.get(id)}` : "";
  return `/api/photo/${id}${view}${ts}`;
};
const side = (id) => {
  return { photo: id, box: photoBaseBox(id) };
};
const normalizeSide = (s) => {
  if (s.box) return { photo: s.photo, box: [...s.box] };
    const p = photo(s.photo),
      top = s.cut ? s.top : 0;
  return { photo: s.photo, box: s.cut ? [p.white[0], top, p.white[1], p.height] : photoBaseBox(s.photo) };
};
const current = () =>
  active ? state.groups.find((g) => g.id === active) : draft;
const message = (text, error = false) => {
  $("#notice").textContent = text;
  $("#notice").classList.toggle("error", error);
};
async function api(path, data, options = {}) {
  const request =
    data === undefined
      ? {}
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Pairing-Token": token,
          },
          body: JSON.stringify(data),
        };
  Object.assign(request, options);
  const response = await fetch(path, request);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "操作沒有完成，請重試");
  return result;
}
function statePayload() {
  return JSON.parse(
    JSON.stringify({
      product: state.product,
      groups: state.groups,
      format: state.format,
      ai_crops: state.ai_crops || {},
      photo_crops: state.photo_crops || {},
    }),
  );
}
function flushPendingSave() {
  saveTimer = null;
  if (!savePending) return saveChain;
  const payload = savePending;
  savePending = null;
  const waiters = saveWaiters.splice(0);
  saveInFlight++;
  const operation = saveChain
    .catch(() => {})
    .then(() => api("/api/state", payload));
  saveChain = operation;
  operation
    .then(() => {
      if (!savePending) $("#save-status").textContent = "已在本機保存";
      waiters.forEach(({ resolve }) => resolve());
    })
    .catch((error) => {
      $("#save-status").textContent = "尚未保存";
      message(error.message, true);
      waiters.forEach(({ reject }) => reject(error));
    })
    .finally(() => {
      saveInFlight--;
      if (savePending && !saveTimer) saveTimer = setTimeout(flushPendingSave, 220);
    });
  return operation;
}
function flushSaves() {
  clearTimeout(saveTimer);
  saveTimer = null;
  if (savePending) flushPendingSave();
  return saveChain;
}
function cancelPreviewRequest() {
  clearTimeout(previewTimer);
  previewTimer = null;
  previewController?.abort();
  previewController = null;
}
function persist() {
  $("#save-status").textContent = "正在保存…";
  savePending = statePayload();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushPendingSave, 220);
  const promise = new Promise((resolve, reject) => saveWaiters.push({ resolve, reject }));
  promise.catch(() => {});
  return promise;
}
function invalidateExport() {
  $("#export-result").hidden = true;
  exportBatch = null;
}
function setBusy(value) {
  busy = value;
  $("#photo-grid").inert = value;
  $("#pair-tray").inert = value;
  $("#crop-editors").inert = value;
  $("#single-crop-editor").inert = value;
  updateActions();
}
function updateActions() {
  const previewToggle = $("#preview-crop-toggle"),
    previewReset = $("#preview-crop-reset");
  for (const id of ["folder-button", "files-button", "reset"])
    $(`#${id}`).disabled = busy || aiBusy;
  const single = photoMode === "crop";
  $("#pair-mode").disabled = busy || aiBusy;
  $("#single-crop-mode").disabled = busy || aiBusy || !state.photos.length;
  $("#pair-mode").setAttribute("aria-pressed", String(photoMode === "pair"));
  $("#single-crop-mode").setAttribute("aria-pressed", String(single));
  $("#swap").hidden = single;
  $("#swap").disabled = single || !current() || busy;
  $("#add-pair").disabled = single || !draft || busy;
  $("#add-pair").hidden = single || !!active;
  $("#remove-pair").hidden = single || !active;
  $("#remove-pair").disabled = busy;
  $("#clear-selection").disabled = !selected.length || busy;
  $("#delete-mode").disabled = busy || aiBusy || !state.photos.length;
  $("#delete-mode").textContent = deleteMode ? "完成刪除" : "刪除照片";
  $("#delete-mode").setAttribute("aria-pressed", String(deleteMode));
  $("#delete-selected").hidden = !deleteMode;
  $("#delete-selected").disabled = busy || !selected.length;
  $("#delete-selected").textContent =
    `刪除已選${selected.length ? ` ${selected.length} 張` : ""}`;
  $("#export").disabled = !state.photos.length || busy;
  $("#product").disabled = busy;
  $("#ai-analyze").disabled = !state.photos.length || busy;
  $("#ai-analyze").textContent = aiBusy ? "取消分析" : "分析照片";
  previewToggle.disabled = single || !current() || busy;
  previewToggle.hidden = single || !current();
  previewToggle.textContent = previewCropMode ? "完成裁切" : "選擇裁切";
  previewToggle.setAttribute("aria-pressed", String(previewCropMode));
  previewReset.hidden = !previewCropMode || !current();
  previewReset.disabled = busy;
  $("#single-crop-actions").hidden = !single || !singleCropPhotoId;
  $("#single-crop-reset").disabled = busy || !singleCropDraft;
  $("#single-crop-cancel").disabled = busy;
  $("#single-crop-apply").hidden = !single || !singleCropPhotoId;
  $("#single-crop-apply").disabled = busy || !singleCropDraft;
}
function renderLibrary() {
  const grid = $("#photo-grid");
  const focusedId = grid.contains(document.activeElement)
    ? document.activeElement.dataset.photoId
    : null;
  const used = new Set(
    state.groups.flatMap((g) => [g.left.photo, g.right.photo]),
  );
  const available =
    photoMode === "crop" ? state.photos : state.photos.filter((p) => !used.has(p.id));
  grid.replaceChildren();
  grid.classList.toggle("delete-mode", deleteMode);
  $("#library-empty").hidden = available.length > 0;
  $("#photo-count").textContent = `${state.photos.length} 張`;
  const emptyTitle = $("#library-empty h3"),
    emptyText = $("#library-empty p");
  if (state.photos.length && !available.length) {
    emptyTitle.textContent = "所有照片都已配對";
    emptyText.textContent = "若要重新挑選，請先從下方配對區刪除一組";
  } else {
    emptyTitle.textContent = "把這件商品的照片放進來";
    emptyText.textContent =
      "支援 JPG/JPEG、PNG、WebP、AVIF、BMP、TIFF、靜態 GIF";
  }
  for (const p of available) {
    const item = node("div", null, "photo-item");
    const button = node("button", null, "photo");
    button.dataset.photoId = p.id;
    const cropSelected = photoMode === "crop" && singleCropPhotoId === p.id;
    const position = selected.indexOf(p.id);
    button.setAttribute(
      "aria-label",
      `${deleteMode ? "勾選" : photoMode === "crop" ? cropSelected ? "正在裁切" : "裁切" : "選擇"} ${p.name}`,
    );
    button.setAttribute("aria-pressed", String(cropSelected || selected.includes(p.id)));
    const image = node("img");
    image.src = photoSrc(p.id);
    image.alt = p.name;
    image.loading = "lazy";
    image.draggable = false;
    button.append(
      image,
      node(
        "span",
        deleteMode
          ? position >= 0
            ? "✓"
            : ""
          : photoMode === "crop"
            ? cropSelected
              ? "裁切中"
              : ""
          : position === 0
            ? "左"
            : position === 1
              ? "右"
              : "",
        `selection-marker${photoMode === "crop" ? " crop-selection-marker" : ""}`,
      ),
      node("span", p.name, "photo-name"),
    );
    if (state.photo_crops?.[p.id] || state.ai_crops?.[p.id]) {
      const marker = node("span", null, "crop-marker");
      marker.title = "已裁切";
      marker.setAttribute("aria-label", "已裁切");
      button.append(marker);
    }
    button.onclick = () => {
      if (deleteMode) toggleDeleteSelection(p.id);
      else if (photoMode === "crop") selectSingleCrop(p.id);
      else select(p.id);
    };
    item.append(button);
    if (deleteMode) {
      const remove = node("button", "×", "photo-delete danger");
      remove.type = "button";
      remove.dataset.photoId = p.id;
      remove.setAttribute("aria-label", `刪除照片 ${p.name}`);
      remove.title = `刪除照片 ${p.name}`;
      remove.onclick = (event) => {
        event.stopPropagation();
        deletePhoto(p.id);
      };
      item.append(remove);
    }
    grid.append(item);
    if (p.id === focusedId) button.focus({ preventScroll: true });
  }
  $("#selection-text").textContent =
    photoMode === "crop"
      ? singleCropPhotoId
        ? `正在裁切：${photo(singleCropPhotoId)?.name || "照片"}`
        : "選擇一張照片開始裁切"
      : deleteMode
        ? selected.length
          ? `已選 ${selected.length} 張，按「刪除已選」`
          : "刪除模式已開啟，點擊照片勾選"
        : selected.length
          ? `已選 ${selected.length} / 2 張${selected.length === 2 ? "，右側可預覽並加入拼圖" : "，再選一張"}`
          : "勾選兩張，組成一張左右拼圖";
  renderAiResults();
}
function updateSelectionUI() {
  for (const button of $("#photo-grid").querySelectorAll(".photo")) {
    const position = selected.indexOf(button.dataset.photoId);
    const cropSelected = photoMode === "crop" && singleCropPhotoId === button.dataset.photoId;
    button.setAttribute("aria-pressed", String(cropSelected || position >= 0));
    const marker = button.querySelector(".selection-marker");
    if (marker)
      marker.textContent = deleteMode
        ? position >= 0
          ? "✓"
          : ""
        : photoMode === "crop"
          ? cropSelected
            ? "裁切中"
            : ""
        : position === 0
          ? "左"
          : position === 1
            ? "右"
            : "";
    button.setAttribute(
      "aria-label",
      `${deleteMode ? "勾選" : photoMode === "crop" ? cropSelected ? "正在裁切" : "裁切" : "選擇"} ${photo(button.dataset.photoId)?.name || "照片"}`,
    );
  }
  $("#selection-text").textContent =
    photoMode === "crop"
      ? singleCropPhotoId
        ? `正在裁切：${photo(singleCropPhotoId)?.name || "照片"}`
        : "選擇一張照片開始裁切"
      : deleteMode
        ? selected.length
          ? `已選 ${selected.length} 張，按「刪除已選」`
          : "刪除模式已開啟，點擊照片勾選"
        : selected.length
          ? `已選 ${selected.length} / 2 張${selected.length === 2 ? "，右側可預覽並加入拼圖" : "，再選一張"}`
          : "勾選兩張，組成一張左右拼圖";
  $("#clear-selection").disabled = !selected.length || busy;
  updateActions();
}
function toggleDeleteSelection(id) {
  if (busy || !deleteMode) return;
  selected = selected.includes(id)
    ? selected.filter((photoId) => photoId !== id)
    : [...selected, id];
  updateSelectionUI();
}
async function deleteSelectedPhotos() {
  if (busy || aiBusy || !deleteMode || !selected.length) return;
  const ids = [...selected];
  if (
    !confirm(
      `確定刪除選取的 ${ids.length} 張照片？刪除後不會再出現在目前商品中，已匯出的檔案不受影響。`,
    )
  )
    return;
  setBusy(true);
  message(`正在刪除 ${ids.length} 張照片…`);
  try {
    await api("/api/photos/delete", { photo_ids: ids });
    selected = [];
    invalidateExport();
    await refresh();
    message(`已刪除 ${ids.length} 張照片`);
  } catch (error) {
    message(error.message, true);
  } finally {
    setBusy(false);
  }
}
async function deletePhoto(id) {
  if (busy || aiBusy) return;
  const target = photo(id);
  if (!target) return;
  if (
    !confirm(
      `確定刪除「${target.name}」？刪除後不會再出現在目前商品中，已匯出的檔案不受影響。`,
    )
  )
    return;
  const targetIndex = [
    ...$("#photo-grid").querySelectorAll(".photo"),
  ].findIndex((button) => button.dataset.photoId === id);
  let focusAfterDelete = false;
  setBusy(true);
  message(`正在刪除照片「${target.name}」…`);
  try {
    await api("/api/photo/delete", { photo_id: id });
    selected = selected.filter((photoId) => photoId !== id);
    aiResults = aiResults.filter((result) => result.id !== id);
    draft =
      draft && [draft.left.photo, draft.right.photo].includes(id)
        ? null
        : draft;
    invalidateExport();
    await refresh();
    focusAfterDelete = true;
    message(`已刪除照片「${target.name}」`);
  } catch (error) {
    message(error.message, true);
  } finally {
    setBusy(false);
    if (focusAfterDelete) {
      const deleteButtons = $("#photo-grid").querySelectorAll(".photo-delete");
      (
        deleteButtons[
          Math.min(Math.max(targetIndex, 0), deleteButtons.length - 1)
        ] || $("#delete-mode")
      ).focus({ preventScroll: true });
    }
  }
}
function renderAiResults() {
  const container = $("#ai-results");
  aiResults = aiResults.filter((x) => photo(x.id));
  const proposed = aiResults.filter((x) => x.analysis && x.crop_box[1] > 0);
  const failed = aiResults.filter((x) => !x.analysis);
  if (!proposed.length && !failed.length) {
    container.hidden = true;
    container.replaceChildren();
    return;
  }
  container.hidden = false;
  container.replaceChildren();
  const tools = node("div", null, "ai-tools");
  const selectAllLabel = node("label", null, "ai-select-all");
  const selectAll = node("input");
  selectAll.type = "checkbox";
  selectAll.checked = proposed.length > 0 && proposed.every((x) => x.apply);
  selectAll.indeterminate = proposed.some((x) => x.apply) && !selectAll.checked;
  selectAll.onchange = () => {
    proposed.forEach((x) => {
      x.apply = selectAll.checked;
    });
    renderAiResults();
  };
  selectAllLabel.append(selectAll, node("span", "全選需裁切照片"));
  const filterLabel = node("label", null, "ai-filter-label");
  const filter = node("input");
  filter.type = "checkbox";
  filter.className = "ai-filter";
  filter.checked = aiFilter === "review";
  filter.setAttribute("aria-label", "只看需確認的照片");
  filter.onchange = () => {
    aiFilter = filter.checked ? "review" : "all";
    renderAiResults();
  };
  filterLabel.append(filter, node("span", "只看需確認"));
  tools.append(selectAllLabel, filterLabel);
  const visible =
    aiFilter === "review"
      ? [...proposed.filter((x) => x.issues?.length), ...failed]
      : [...proposed, ...failed];
  const list = node("div", null, "ai-result-list");
  for (const result of visible) {
    const item = node(
      "label",
      null,
      `ai-result${result.analysis ? "" : " ai-result-failed"}`,
    );
    if (result.apply == null) result.apply = true;
    const checkbox = node("input");
    checkbox.type = "checkbox";
    checkbox.checked = !!result.apply;
    checkbox.disabled = !result.analysis;
    checkbox.dataset.photoId = result.id;
    checkbox.onchange = () => {
      result.apply = checkbox.checked;
      renderAiResults();
    };
    const thumb = node("img");
    thumb.src = photoSrc(result.id);
    thumb.alt = result.name;
    thumb.loading = "lazy";
    const detail = node("span", null, "ai-result-detail");
    detail.append(node("strong", result.name));
    const analysis = result.analysis;
    const status = !analysis
      ? "辨識失敗"
      : result.applied
        ? "已套用裁切建議"
        : `建議移除上方 ${result.crop_box[1]} px`;
    detail.append(node("span", status));
    if (result.issues?.length)
      detail.append(node("small", result.issues.join("；")));
    item.append(checkbox, thumb, detail);
    list.append(item);
  }
  const apply = node("button", "套用裁切", "primary");
  apply.type = "button";
  apply.disabled = !aiResults.some(
    (x) => x.apply && x.analysis && x.crop_box[1] > 0,
  );
  apply.onclick = applyAiCrops;
  container.append(tools, list, apply);
}
async function applyAiCrops() {
  const selectedIds = new Set(
    aiResults
      .filter((x) => photo(x.id) && x.apply && x.analysis && x.crop_box[1] > 0)
      .map((x) => x.id),
  );
  if (!selectedIds.size) {
    message("請先勾選至少一張有裁切建議的照片", true);
    return;
  }
  const topById = new Map(
    aiResults
      .filter((x) => selectedIds.has(x.id))
      .map((x) => [x.id, x.crop_box[1]]),
  );
  state.ai_crops = state.ai_crops || {};
  state.photo_crops = state.photo_crops || {};
  const appliedIds = new Set();
  for (const [id, top] of topById) {
    const p = photo(id);
    if (!p) continue;
    state.ai_crops[id] = top;
    appliedIds.add(id);
  }
  for (const [id, top] of topById) {
    const p = photo(id);
    if (!p) continue;
    state.photo_crops[id] = [p.white[0], top, p.white[1], p.height];
  }
  for (const result of aiResults)
    if (appliedIds.has(result.id)) result.applied = true;
  for (const id of appliedIds) {
    cropVersions.set(id, Date.now());
    for (const group of state.groups)
      if ([group.left.photo, group.right.photo].includes(id))
        invalidatePairPreview(group.id);
  }
  for (const group of state.groups)
    for (const sideKey of ["left", "right"]) {
      const s = group[sideKey],
        p = photo(s.photo),
        top = topById.get(s.photo);
      if (p && appliedIds.has(s.photo) && top != null)
        s.box = [p.white[0], top, p.white[1], p.height];
    }
  if (draft)
    for (const sideKey of ["left", "right"]) {
      const s = draft[sideKey],
        p = photo(s.photo),
        top = topById.get(s.photo);
      if (p && appliedIds.has(s.photo) && top != null)
        s.box = [p.white[0], top, p.white[1], p.height];
    }
  invalidateExport();
  renderLibrary();
  renderTray();
  renderEditor();
  for (const group of state.groups)
    if ([group.left.photo, group.right.photo].some((id) => appliedIds.has(id)))
      requestPairPreview(group);
  try {
    await persist();
  } catch (error) {
    message("裁切已更新但保存失敗：" + error.message, true);
    return;
  }
  message(
    `已套用 ${selectedIds.size} 張 AI 裁切建議，之後新增的組圖也會沿用，可再手動微調`,
  );
}
function cancelAiAnalysis(showStatus = true) {
  if (!aiBusy) return;
  const runId = aiRunId;
  aiRunToken++;
  aiController?.abort();
  aiController = null;
  aiRunId = null;
  if (runId) api("/api/ai-cancel", { run_id: runId }).catch(() => {});
  aiBusy = false;
  aiResults = [];
  aiLogFile = null;
  aiFilter = "all";
  $("#ai-progress").textContent = showStatus ? "分析已取消" : "";
  $("#ai-progress").title = "";
  renderAiResults();
  message("");
  updateActions();
}
function select(id) {
  if (busy) return;
  if (active) {
    active = null;
    selected = [];
    previewCropMode = false;
    previewBaseSize = null;
    previewCropBeforeEdit = null;
  }
  if (selected.includes(id)) selected = selected.filter((x) => x !== id);
  else if (selected.length < 2) selected.push(id);
  else {
    message("已選兩張，先加入拼圖或取消其中一張");
    return;
  }
  draft =
    selected.length === 2
      ? {
          id: crypto.randomUUID(),
          left: side(selected[0]),
          right: side(selected[1]),
        }
      : null;
  updateSelectionUI();
  renderTray();
  renderEditor();
}
function renderTray() {
  $("#group-count").textContent = `${state.groups.length} 組`;
  const tray = $("#pair-tray");
  const focusedId = tray.contains(document.activeElement)
    ? document.activeElement.dataset.groupId
    : null;
  const focusedDelete =
    document.activeElement.classList.contains("pair-delete");
  tray.replaceChildren();
  if (!state.groups.length)
    tray.append(node("p", "配對完成的照片會放在這裡", "tray-empty"));
  state.groups.forEach((g, index) => {
    const item = node("div", null, "pair-item");
    const button = node("button", null, "pair-card");
    button.setAttribute("aria-label", `編輯第 ${index + 1} 組`);
    button.setAttribute("aria-current", String(active === g.id));
    button.dataset.groupId = g.id;
    const mini = node("span", null, "pair-mini");
    const cached = pairPreviewCache.get(g.id);
    if (cached?.key === pairPreviewKey(g)) {
      const image = node("img", null, "pair-preview");
      image.src = cached.image;
      image.alt = `第 ${index + 1} 組拼圖預覽`;
      mini.append(image);
    } else {
      for (const s of [g.left, g.right]) {
        const image = node("img");
        image.src = photoSrc(s.photo);
        image.alt = photo(s.photo).name;
        mini.append(image);
      }
    }
    button.append(mini, node("span", `第 ${index + 1} 組`, "pair-label"));
    button.onclick = () => {
      photoMode = "pair";
      deleteMode = false;
      clearSingleCrop();
      active = g.id;
      selected = [];
      draft = null;
      previewCropMode = false;
      previewBaseSize = null;
      previewCropBeforeEdit = null;
      renderLibrary();
      renderTray();
      renderEditor();
    };
    const remove = node("button", null, "pair-delete");
    remove.type = "button";
    remove.dataset.groupId = g.id;
    remove.setAttribute("aria-label", `刪除第 ${index + 1} 組`);
    remove.title = `刪除第 ${index + 1} 組`;
    remove.innerHTML = '<svg aria-hidden="true"><use href="#i-close"/></svg>';
    remove.onclick = () => removePair(g.id);
    item.append(button, remove);
    tray.append(item);
    if (g.id === focusedId)
      (focusedDelete ? remove : button).focus({ preventScroll: true });
  });
}
function removePair(id) {
  if (busy) return;
  const index = state.groups.findIndex((g) => g.id === id);
  if (index < 0) return;
  const focusInTray = $("#pair-tray").contains(document.activeElement);
  state.groups.splice(index, 1);
  invalidatePairPreview(id);
  if (active === id) {
    active = null;
    selected = [];
    draft = null;
    previewCropMode = false;
    previewBaseSize = null;
    previewCropBeforeEdit = null;
  }
  invalidateExport();
  renderLibrary();
  renderTray();
  renderEditor();
  persist().catch(() => {});
  if (focusInTray) {
    const buttons = $("#pair-tray").querySelectorAll(".pair-delete");
    (buttons[Math.min(index, buttons.length - 1)] || $("#files-button")).focus({
      preventScroll: true,
    });
  }
  message("已刪除這組配對，原照片與已匯出的檔案仍保留");
}
function singleCropBox() {
  return singleCropDraft?.box || null;
}
function singleCropDirty() {
  if (!singleCropDraft || !singleCropBeforeEdit) return false;
  return JSON.stringify(singleCropDraft.box) !== JSON.stringify(singleCropBeforeEdit);
}
function selectSingleCrop(id) {
  if (busy || photoMode !== "crop") return;
  if (singleCropDirty() && !confirm("目前裁切尚未套用，要捨棄這次調整嗎？")) return;
  singleCropPhotoId = id;
  singleCropBeforeEdit = photoBaseBox(id);
  singleCropDraft = { photo: id, box: [...singleCropBeforeEdit] };
  selected = [];
  draft = null;
  active = null;
  previewCropMode = false;
  previewBaseSize = null;
  renderLibrary();
  renderEditor();
}
function clearSingleCrop() {
  singleCropPhotoId = null;
  singleCropDraft = null;
  singleCropBeforeEdit = null;
  $("#single-crop-editor").replaceChildren();
}
async function applySingleCrop() {
  if (busy || !singleCropDraft) return;
  const id = singleCropPhotoId;
  const box = [...singleCropDraft.box];
  const original = [0, 0, photo(id).width, photo(id).height];
  const isOriginal = JSON.stringify(box) === JSON.stringify(original);
  state.photo_crops = state.photo_crops || {};
  if (isOriginal) delete state.photo_crops[id];
  else state.photo_crops[id] = box;
  cropVersions.set(id, Date.now());
  delete (state.ai_crops || {})[id];
  for (const group of state.groups)
    for (const key of ["left", "right"])
      if (group[key].photo === id) group[key].box = [...box];
  if (draft)
    for (const key of ["left", "right"])
      if (draft[key].photo === id) draft[key].box = [...box];
  invalidateExport();
  singleCropBeforeEdit = [...box];
  renderLibrary();
  const affectedGroups = state.groups.filter((group) =>
    [group.left.photo, group.right.photo].includes(id),
  );
  affectedGroups.forEach((group) => invalidatePairPreview(group.id));
  renderTray();
  renderEditor();
  try {
    await persist();
  } catch (error) {
    message("裁切已更新但保存失敗：" + error.message, true);
    return;
  }
  affectedGroups.forEach(requestPairPreview);
  message(`已套用裁切${state.groups.filter((g) => [g.left.photo, g.right.photo].includes(id)).length ? "，相關拼圖也已更新" : ""}`);
}
function cancelSingleCrop() {
  if (singleCropDirty() && !confirm("目前裁切尚未套用，要捨棄這次調整嗎？")) return;
  clearSingleCrop();
  renderLibrary();
  renderEditor();
}
function resetSingleCrop() {
  if (!singleCropDraft || !singleCropPhotoId) return;
  singleCropDraft.box = [0, 0, photo(singleCropPhotoId).width, photo(singleCropPhotoId).height];
  renderEditor();
}
function renderEditor() {
  const singleMode = photoMode === "crop";
  const single = singleMode && singleCropDraft;
  $("#editor-title").textContent = singleMode ? "單張照片裁切" : "拼圖預覽";
  $("#preview-empty p").textContent = singleMode
    ? "單張照片裁切"
    : "兩張照片，一張拼圖";
  $("#preview-empty span").textContent = singleMode
    ? "從左側選一張照片開始"
    : "依勾選順序放在左、右兩側";
  $("#single-crop-editor").hidden = !single;
  $("#single-crop-editor").replaceChildren();
  if (single) $("#single-crop-editor").append(cropEditor(singleCropDraft, "single", { single: true }));
  const group = current();
  $("#editor-subtitle").textContent = active
    ? `正在調整第 ${state.groups.findIndex((g) => g.id === active) + 1} 組`
    : group
      ? "確認這個組合，再按「加入拼圖」"
      : single
        ? "調整照片範圍後套用，會同步更新相關拼圖"
        : singleMode
          ? "先從左側選一張照片"
          : "先從左側選兩張照片";
  $("#crop-details").hidden = single || !group;
  $("#crop-editors").replaceChildren();
  if (group)
    for (const key of ["left", "right"])
      $("#crop-editors").append(cropEditor(group[key], key));
  updateActions();
  if (singleMode) {
    $("#crop-editors").replaceChildren();
    scheduleSinglePreview(0);
  } else schedulePreview(0);
}
function cropEditor(s, key, options = {}) {
  const p = photo(s.photo),
    single = options.single === true,
    label = single ? "照片" : key === "left" ? "左圖" : "右圖",
    bounds = [p.white[0], 0, p.white[1], p.height],
    old = normalizeSide(s);
  delete s.top;
  delete s.cut;
  s.box = old.box;
  const panel = node("div", null, "crop-editor");
  panel.append(node("h3", `${label} · ${p.name}`));
  const stage = node("div", null, "crop-stage"),
    image = node("img"),
    box = node("div", null, "crop-box");
  image.src = `/api/photo/${p.id}`;
  image.alt = `${label}照片與裁切框`;
  image.draggable = false;
  const handles = {};
  for (const name of [
    "left",
    "right",
    "top",
    "bottom",
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
  ]) {
    const h = node("button", null, `crop-handle ${name}`);
    h.type = "button";
    const corner = name.includes("-");
    h.setAttribute("role", corner ? "button" : "slider");
    h.setAttribute(
      "aria-label",
      `${label}裁切${name === "left" ? "左邊" : name === "right" ? "右邊" : name === "top" ? "上邊" : name === "bottom" ? "下邊" : { "top-left": "左上角", "top-right": "右上角", "bottom-left": "左下角", "bottom-right": "右下角" }[name]}${corner ? "" : "（拖曳控制）"}`,
    );
    box.append(h);
    handles[name] = h;
  }
  stage.append(image, box);
  panel.append(stage);
  const controls = node("div", null, "crop-controls"),
    readout = node("div", null, "crop-readout"),
    amount = node("span"),
    reset = node("button", "恢復原始範圍", "crop-reset");
  reset.type = "button";
  reset.hidden = single;
  reset.setAttribute("aria-label", `${label}恢復原始裁切範圍`);
  controls.append(readout, reset);
  readout.append(amount);
  panel.append(controls);
  const minSize = 20,
    index = { left: 0, top: 1, right: 2, bottom: 3 };
  function clampBox(next) {
    next[0] = Math.max(bounds[0], Math.min(next[2] - minSize, next[0]));
    next[2] = Math.min(bounds[2], Math.max(next[0] + minSize, next[2]));
    next[1] = Math.max(bounds[1], Math.min(next[3] - minSize, next[1]));
    next[3] = Math.min(bounds[3], Math.max(next[1] + minSize, next[3]));
    return next;
  }
  function sync() {
    const [left, top, right, bottom] = s.box,
      width = bounds[2] - bounds[0];
    box.style.left = `${((left - bounds[0]) / width) * 100}%`;
    box.style.top = `${(top / p.height) * 100}%`;
    box.style.width = `${((right - left) / width) * 100}%`;
    box.style.height = `${((bottom - top) / p.height) * 100}%`;
    amount.textContent = `裁切範圍：${right - left} × ${bottom - top} px`;
    for (const name of ["left", "right", "top", "bottom"])
      handles[name].setAttribute("aria-valuenow", String(s.box[index[name]]));
  }
  function changed() {
    s.box = clampBox(s.box);
    sync();
    invalidateExport();
    if (active && !single) invalidatePairPreview(active);
    if (single) scheduleSinglePreview();
    else schedulePreview();
    if (active && !single) persist();
  }
  function move(name, x, y) {
    const next = [...s.box];
    if (name === "left" || name === "right") next[index[name]] = Math.round(x);
    else if (name === "top" || name === "bottom")
      next[index[name]] = Math.round(y);
    else {
      const [v, h] = name.split("-");
      next[index[v]] = Math.round(y);
      next[index[h]] = Math.round(x);
    }
    s.box = next;
    changed();
  }
  reset.onclick = () => {
    s.box = [...bounds];
    changed();
  };
  for (const name of Object.keys(handles)) {
    const h = handles[name],
      corner = name.includes("-");
    h.onkeydown = (event) => {
      const keys = [
        "ArrowLeft",
        "ArrowRight",
        "ArrowUp",
        "ArrowDown",
        "Home",
        "End",
      ];
      if (!keys.includes(event.key)) return;
      event.preventDefault();
      const next = [...s.box],
        step = event.shiftKey ? 10 : 1,
        [v, hor] = corner ? name.split("-") : [name, null];
      const set = (axis) => {
        const i = index[axis],
          [min, max] =
            axis === "left" || axis === "right"
              ? [bounds[0], bounds[2]]
              : [bounds[1], bounds[3]];
        next[i] =
          event.key === "Home"
            ? min
            : event.key === "End"
              ? max
              : next[i] +
                (event.key ===
                (axis === "top" || axis === "bottom"
                  ? "ArrowDown"
                  : "ArrowRight")
                  ? step
                  : -step);
      };
      set(v);
      if (hor) set(hor);
      s.box = next;
      changed();
    };
    h.onpointerdown = (event) => {
      event.preventDefault();
      h.setPointerCapture(event.pointerId);
    };
    h.onpointermove = (event) => {
      if (!h.hasPointerCapture(event.pointerId)) return;
      const r = stage.getBoundingClientRect();
      const x =
        bounds[0] +
        ((event.clientX - r.left) / r.width) * (bounds[2] - bounds[0]);
      const y = bounds[1] + ((event.clientY - r.top) / r.height) * p.height;
      move(name, x, y);
    };
    h.onpointerup = (event) => {
      if (h.hasPointerCapture(event.pointerId))
        h.releasePointerCapture(event.pointerId);
    };
  }
  sync();
  return panel;
}
const PREVIEW_HANDLES = [
  "left",
  "right",
  "top",
  "bottom",
  "top-left",
  "top-right",
  "bottom-left",
  "bottom-right",
];
let previewHandles = {};
function previewCropValue(group) {
  return group.preview_crop ? [...group.preview_crop] : [0, 0, 10000, 10000];
}
function previewCropClamp(next) {
  const [width, height] = previewBaseSize || [1024, 1024];
  const minX = Math.min(10000, Math.ceil(200000 / Math.max(1, width))),
    minY = Math.min(10000, Math.ceil(200000 / Math.max(1, height)));
  next[0] = Math.max(0, Math.min(next[2] - minX, next[0]));
  next[2] = Math.min(10000, Math.max(next[0] + minX, next[2]));
  next[1] = Math.max(0, Math.min(next[3] - minY, next[1]));
  next[3] = Math.min(10000, Math.max(next[1] + minY, next[3]));
  return next;
}
function syncPreviewCropBox() {
  const box = $("#preview-crop-box"),
    group = current();
  if (!box || !group || !previewCropMode || !previewBaseSize) {
    if (box) box.hidden = true;
    return;
  }
  const [left, top, right, bottom] = previewCropValue(group);
  box.hidden = false;
  box.style.left = `${left / 100}%`;
  box.style.top = `${top / 100}%`;
  box.style.width = `${(right - left) / 100}%`;
  box.style.height = `${(bottom - top) / 100}%`;
  const values = { left, right, top, bottom };
  for (const name of ["left", "right", "top", "bottom"])
    previewHandles[name]?.setAttribute("aria-valuenow", String(values[name]));
}
function movePreviewCrop(name, x, y) {
  const group = current();
  if (!group || !previewCropMode) return;
  const next = previewCropValue(group);
  if (name === "left" || name === "right")
    next[{ left: 0, right: 2 }[name]] = Math.round(x);
  else if (name === "top" || name === "bottom")
    next[{ top: 1, bottom: 3 }[name]] = Math.round(y);
  else {
    const [vertical, horizontal] = name.split("-");
    next[{ left: 0, right: 2 }[horizontal]] = Math.round(x);
    next[{ top: 1, bottom: 3 }[vertical]] = Math.round(y);
  }
  group.preview_crop = previewCropClamp(next);
  invalidatePairPreview(group.id);
  syncPreviewCropBox();
  invalidateExport();
  schedulePreview();
  if (active) persist();
}
function initPreviewCrop() {
  const box = $("#preview-crop-box");
  if (!box) return;
  const labels = {
    left: "左邊",
    right: "右邊",
    top: "上邊",
    bottom: "下邊",
    "top-left": "左上角",
    "top-right": "右上角",
    "bottom-left": "左下角",
    "bottom-right": "右下角",
  };
  for (const name of PREVIEW_HANDLES) {
    const handle = node("button", null, `preview-crop-handle ${name}`);
    handle.type = "button";
    const corner = name.includes("-");
    handle.setAttribute("role", corner ? "button" : "slider");
    handle.setAttribute(
      "aria-label",
      `預覽裁切${labels[name]}${corner ? "" : "（拖曳控制）"}`,
    );
    box.append(handle);
    previewHandles[name] = handle;
    handle.onkeydown = (event) => {
      const group = current();
      if (!group || !previewBaseSize) return;
      const next = previewCropValue(group),
        stepX = ((event.shiftKey ? 10 : 1) * 10000) / previewBaseSize[0],
        stepY = ((event.shiftKey ? 10 : 1) * 10000) / previewBaseSize[1];
      const set = (axis) => {
        const i = { left: 0, right: 2, top: 1, bottom: 3 }[axis],
          horizontal = axis === "left" || axis === "right",
          step = horizontal ? stepX : stepY;
        const delta =
          event.key === "Home"
            ? 0
            : event.key === "End"
              ? 10000
              : next[i] +
                (event.key === (horizontal ? "ArrowRight" : "ArrowDown")
                  ? step
                  : -step);
        next[i] = Math.round(delta);
      };
      if (
        ![
          "ArrowLeft",
          "ArrowRight",
          "ArrowUp",
          "ArrowDown",
          "Home",
          "End",
        ].includes(event.key)
      )
        return;
      event.preventDefault();
      const [vertical, horizontal] = corner ? name.split("-") : [name, null];
      set(vertical);
      if (horizontal) set(horizontal);
      group.preview_crop = previewCropClamp(next);
      invalidatePairPreview(group.id);
      syncPreviewCropBox();
      invalidateExport();
      schedulePreview();
      if (active) persist();
    };
    handle.onpointerdown = (event) => {
      event.preventDefault();
      handle.setPointerCapture(event.pointerId);
    };
    handle.onpointermove = (event) => {
      if (!handle.hasPointerCapture(event.pointerId)) return;
      const frame = $("#preview-frame"),
        rect = frame.getBoundingClientRect();
      movePreviewCrop(
        name,
        ((event.clientX - rect.left) / rect.width) * 10000,
        ((event.clientY - rect.top) / rect.height) * 10000,
      );
    };
    handle.onpointerup = (event) => {
      if (handle.hasPointerCapture(event.pointerId))
        handle.releasePointerCapture(event.pointerId);
    };
  }
}
function scheduleSinglePreview(delay = 80) {
  cancelPreviewRequest();
  const serial = ++previewSerial;
  if (!singleCropDraft) {
    $("#preview-frame").hidden = true;
    $("#preview-empty").hidden = false;
    $("#preview-crop-box").hidden = true;
    $("#preview-stage").removeAttribute("aria-busy");
    $("#preview-loading").hidden = true;
    return;
  }
  previewTimer = setTimeout(async () => {
    $("#preview-stage").setAttribute("aria-busy", "true");
    $("#preview-loading").hidden = false;
    const controller = new AbortController();
    previewController = controller;
    try {
      const result = await api("/api/photo-preview", {
        photo_id: singleCropPhotoId,
        box: singleCropDraft.box,
      }, { signal: controller.signal });
      if (serial !== previewSerial) return;
      previewBaseSize = result.size;
      $("#preview").src = result.image;
      $("#preview").alt = "目前單張照片的裁切預覽";
      $("#preview-frame").hidden = false;
      $("#preview-empty").hidden = true;
      $("#preview-crop-box").hidden = true;
    } catch (error) {
      if (error.name !== "AbortError" && serial === previewSerial) message(error.message, true);
    } finally {
      if (previewController === controller) previewController = null;
      if (serial === previewSerial)
        $("#preview-loading").hidden = true;
      if (serial === previewSerial)
        $("#preview-stage").removeAttribute("aria-busy");
    }
  }, delay);
}
function schedulePreview(delay = 160) {
  cancelPreviewRequest();
  const serial = ++previewSerial,
    group = current();
  if (!group) {
    $("#preview-frame").hidden = true;
    $("#preview-empty").hidden = false;
    $("#preview-crop-box").hidden = true;
    $("#preview-stage").removeAttribute("aria-busy");
    $("#preview-loading").hidden = true;
    return;
  }
  previewTimer = setTimeout(async () => {
    $("#preview-stage").setAttribute("aria-busy", "true");
    $("#preview-loading").hidden = false;
    const controller = new AbortController();
    previewController = controller;
    try {
      const requestGroup = JSON.parse(JSON.stringify(group));
      if (previewCropMode) delete requestGroup.preview_crop;
      const result = await api("/api/preview", {
        group: requestGroup,
        format: state.format,
      }, { signal: controller.signal });
      if (serial !== previewSerial) return;
      previewBaseSize = result.size;
      $("#preview").src = result.image;
      $("#preview-frame").hidden = false;
      $("#preview-empty").hidden = true;
      if (!previewCropMode) {
        pairPreviewCache.set(group.id, {
          key: pairPreviewKey(group),
          image: result.image,
        });
        renderTray();
      }
      requestAnimationFrame(syncPreviewCropBox);
    } catch (error) {
      if (error.name !== "AbortError" && serial === previewSerial) {
        $("#preview-frame").hidden = true;
        $("#preview-empty").hidden = false;
        $("#preview-crop-box").hidden = true;
        message(error.message, true);
      }
    } finally {
      if (previewController === controller) previewController = null;
      if (serial === previewSerial)
        $("#preview-loading").hidden = true;
      if (serial === previewSerial)
        $("#preview-stage").removeAttribute("aria-busy");
    }
  }, delay);
}
function renderAll() {
  $("#product").value = state.product;
  renderLibrary();
  renderTray();
  renderEditor();
}
async function refresh() {
  state = await api("/api/state");
  state.format = "natural";
  token = state.token;
  renderAll();
}
async function importFiles(list) {
  if (busy || !list.length) return;
  const files = [...list].sort((a, b) =>
    a.name.localeCompare(b.name, "zh-Hant", { numeric: true }),
  );
  const folderNames = new Set(
    files
      .map((f) =>
        (f.webkitRelativePath || "")
          .replace(/\\/g, "/")
          .split("/")
          .slice(0, -1)
          .join("/"),
      )
      .filter(Boolean),
  );
  if (folderNames.size > 1) {
    message("請一次選一件商品的資料夾，不要混入多個子資料夾", true);
    return;
  }
  if (
    state.photos.length &&
    !confirm("將這些照片加入目前商品？若是另一件商品，請取消並按「開始新商品」")
  )
    return;
  setBusy(true);
  const issues = [];
  let count = 0,
    duplicates = 0;
  try {
    await flushSaves();
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      if (!/\.(jpe?g|png|webp|avif|bmp|tiff?|gif)$/i.test(file.name)) {
        issues.push(`${file.name}：不是支援的圖片格式`);
        continue;
      }
      if (file.size > 40_000_000) {
        issues.push(`${file.name}：超過 40 MB`);
        continue;
      }
      message(`正在匯入 ${i + 1} / ${files.length}：${file.name}`);
      try {
        const response = await fetch(
          `/api/import?name=${encodeURIComponent(file.name)}`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/octet-stream",
              "X-Pairing-Token": token,
            },
            body: file,
          },
        );
        const result = await response.json();
        if (!response.ok) throw new Error(result.error);
        if (result.duplicate) duplicates++;
        else count++;
      } catch (error) {
        issues.push(`${file.name}：${error.message}`);
      }
    }
    const initial = state.photos.length === 0;
    await refresh();
    if (initial && folderNames.size === 1) {
      state.product = [...folderNames][0].replace(/\\/g, "/").split("/").pop().slice(0, 80);
      $("#product").value = state.product;
      await persist();
    }
    showImportResult(count, duplicates, issues);
  } catch (error) {
    message(error.message, true);
  } finally {
    setBusy(false);
    $("#folder-input").value = "";
    $("#files-input").value = "";
  }
}
function showImportResult(count, duplicates, issues) {
  const details = $("#import-issues");
  details.hidden = !issues.length;
  details
    .querySelector("ul")
    .replaceChildren(...issues.map((text) => node("li", text)));
  message(
    `已匯入 ${count} 張${duplicates ? `，略過 ${duplicates} 張完全重複照片` : ""}${issues.length ? `，另有 ${issues.length} 個檔案未匯入` : ""}`,
    issues.length > 0,
  );
}
$("#folder-button").onclick = () => $("#folder-input").click();
$("#files-button").onclick = () => $("#files-input").click();
$("#folder-input").onchange = (event) => importFiles(event.target.files);
$("#files-input").onchange = (event) => importFiles(event.target.files);
function setPhotoMode(mode) {
  if (busy || aiBusy || (mode === "crop" && !state.photos.length)) return;
  if (photoMode === "crop" && singleCropDirty() && !confirm("目前裁切尚未套用，要捨棄這次調整嗎？")) return;
  photoMode = mode;
  deleteMode = mode === "delete";
  clearSingleCrop();
  selected = [];
  draft = null;
  active = null;
  previewCropMode = false;
  previewBaseSize = null;
  updateActions();
  renderLibrary();
  renderTray();
  renderEditor();
}
$("#pair-mode").onclick = () => setPhotoMode("pair");
$("#single-crop-mode").onclick = () => setPhotoMode("crop");
$("#delete-mode").onclick = () => {
  setPhotoMode(deleteMode ? "pair" : "delete");
};
$("#single-crop-reset").onclick = resetSingleCrop;
$("#single-crop-cancel").onclick = cancelSingleCrop;
$("#single-crop-apply").onclick = applySingleCrop;
$("#delete-selected").onclick = deleteSelectedPhotos;
$("#clear-selection").onclick = () => {
  selected = [];
  draft = null;
  previewCropMode = false;
  previewBaseSize = null;
  renderLibrary();
  renderEditor();
};
$("#add-pair").onclick = async () => {
  if (!draft) return;
  const existing = state.groups.find(
    (g) =>
      [g.left.photo, g.right.photo].sort().join() ===
      [draft.left.photo, draft.right.photo].sort().join(),
  );
  if (existing) {
    message("這兩張已經配成一組，可從下方點選編輯");
    return;
  }
  state.groups.push(draft);
  active = draft.id;
  draft = null;
  selected = [];
  invalidateExport();
  renderAll();
  await persist().catch(() => {});
  message("已加入拼圖，繼續勾選下一組照片");
};
$("#swap").onclick = () => {
  const g = current();
  if (!g) return;
  [g.left, g.right] = [g.right, g.left];
  if (!active) selected.reverse();
  invalidateExport();
  renderLibrary();
  renderTray();
  renderEditor();
  if (active) persist();
};
$("#remove-pair").onclick = () => removePair(active);
$("#product").oninput = (event) => {
  state.product = event.target.value;
  invalidateExport();
  persist();
};
$("#preview-crop-toggle").onclick = () => {
  if (!current()) return;
  previewCropMode = !previewCropMode;
  if (previewCropMode) {
    previewCropBeforeEdit = current().preview_crop
      ? [...current().preview_crop]
      : null;
    if (!current().preview_crop) current().preview_crop = [0, 0, 10000, 10000];
  } else previewCropBeforeEdit = null;
  invalidatePairPreview(current().id);
  renderTray();
  updateActions();
  schedulePreview(0);
  if (active) persist();
};
$("#preview-crop-reset").onclick = () => {
  const group = current();
  if (!group) return;
  if (previewCropBeforeEdit) group.preview_crop = [...previewCropBeforeEdit];
  else delete group.preview_crop;
  previewCropMode = false;
  previewBaseSize = null;
  previewCropBeforeEdit = null;
  invalidatePairPreview(group.id);
  renderTray();
  $("#preview-crop-box").hidden = true;
  updateActions();
  invalidateExport();
  schedulePreview(0);
  if (active) persist();
};
$("#reset").onclick = async () => {
  if (
    !confirm(
      "開始新商品會清空目前的勾選與配對，原始檔案與已匯出的成品不受影響。要繼續嗎？",
    )
  )
    return;
  const cleanup =
    state.photos.length > 0 &&
    confirm(
      "是否同時刪除上一批匯入後的本機工作副本？這不會刪除原始檔案或已匯出的成品。",
    );
  setBusy(true);
  cancelAiAnalysis(false);
  aiResults = [];
  aiLogFile = null;
  aiFilter = "all";
  deleteMode = false;
  $("#ai-progress").textContent = "";
  $("#ai-progress").title = "";
  renderAiResults();
  try {
    await flushSaves();
    await api("/api/reset", { cleanup });
    active = null;
    draft = null;
    selected = [];
    photoMode = "pair";
    clearSingleCrop();
    previewCropMode = false;
    previewBaseSize = null;
    previewCropBeforeEdit = null;
    invalidateExport();
    await refresh();
    $("#import-issues").hidden = true;
    message(
      cleanup
        ? "可以匯入下一件商品的照片，上一批工作副本已清理"
        : "可以匯入下一件商品的照片",
    );
  } catch (error) {
    message(error.message, true);
  } finally {
    setBusy(false);
  }
};
$("#ai-analyze").onclick = async () => {
  if (busy || !state.photos.length) return;
  if (aiBusy) {
    cancelAiAnalysis();
    return;
  }
  try {
    const health = await api("/api/ai-health");
    if (!health.ok) {
      const detail = health.detail || "AI 服務不可用，請先啟動 LM Studio 並載入模型";
      $("#ai-progress").textContent = `無法開始分析：${detail}`;
      message(detail, true);
      return;
    }
  } catch (error) {
    $("#ai-progress").textContent = `無法開始分析：${error.message}`;
    message("無法連線到 AI 服務：" + error.message, true);
    return;
  }
  aiBusy = true;
  aiController = new AbortController();
  const runToken = ++aiRunToken;
  const runId = crypto.randomUUID();
  aiRunId = runId;
  aiLogFile = null;
  aiResults = [];
  $("#ai-progress").textContent = `準備分析 ${state.photos.length} 張照片…`;
  $("#ai-progress").title = "";
  renderAiResults();
  updateActions();
  try {
    const photos = [...state.photos];
    for (let index = 0; index < photos.length; index++) {
      if (runToken !== aiRunToken) return;
      const progress = `正在分析第 ${index + 1} / ${photos.length} 張：${photos[index].name}`;
      message(`AI ${progress}`);
      $("#ai-progress").textContent = progress;
      const result = await api(
        "/api/ai-crop",
        {
          photo_ids: [photos[index].id],
          run_id: runId,
          batch_index: index + 1,
          batch_total: photos.length,
        },
        { signal: aiController.signal },
      );
      if (runToken !== aiRunToken || result.cancelled) return;
      if (result.log_file) aiLogFile = result.log_file;
      const item = result.results[0];
      item.apply = !!item.analysis && item.crop_box[1] > 0;
      aiResults.push(item);
      renderAiResults();
      updateActions();
    }
    const proposed = aiResults.filter((x) => x.analysis && x.crop_box[1] > 0);
    const safe = proposed.filter((x) => !x.issues?.length).length;
    const failed = aiResults.filter((x) => !x.analysis).length;
    const counts = proposed.length || failed
      ? `安全 ${safe} 張、需確認 ${proposed.length - safe} 張、辨識失敗 ${failed} 張`
      : "沒有裁切建議";
    $("#ai-progress").textContent =
      `分析完成 ${aiResults.length} / ${photos.length} 張（${counts}）` +
      (aiLogFile ? " · 診斷紀錄已保存於 logs 資料夾" : "");
    $("#ai-progress").title = aiLogFile ? `診斷紀錄：${aiLogFile}` : "";
    message("");
  } catch (error) {
    if (error.name !== "AbortError" && runToken === aiRunToken) {
      $("#ai-progress").textContent = `分析未完成：${error.message}`;
      message(error.message, true);
    }
  } finally {
    if (runToken === aiRunToken) {
      aiBusy = false;
      aiController = null;
      aiRunId = null;
      updateActions();
    }
  }
};
$("#export").onclick = async () => {
  if (!state.photos.length) return;
  const restoreExportFocus = document.activeElement === $("#export");
  setBusy(true);
  exporting = true;
  $("#export span").textContent = "正在匯出…";
  message("正在從原尺寸照片產生照片與拼圖，原檔不會被覆寫");
  try {
    await persist();
    const result = await api("/api/export", {});
    exportBatch = result.batch;
    $("#export-title").textContent =
      `${result.reused ? "圖片沒有變更，沿用上次匯出" : "已匯出"} ${result.count} 張圖片（照片 ${result.photo_count} 張、拼圖 ${result.collage_count} 張）`;
    $("#export-path").textContent = result.folder;
    $("#export-result").hidden = false;
    let openError = null;
    try {
      await api("/api/open-export", { batch: result.batch });
    } catch (error) {
      openError = error;
    }
    message(
      openError
        ? `已完成 ${result.count} 張圖片，但無法自動開啟資料夾，請按「開啟輸出資料夾」`
        : result.reused
          ? "圖片沒有變更，已沿用上次匯出的資料夾並自動開啟"
          : `已完成 ${result.count} 張圖片，已自動開啟輸出資料夾`,
      !!openError,
    );
  } catch (error) {
    message(error.message, true);
  } finally {
    exporting = false;
    $("#export span").textContent = "匯出所有圖片";
    setBusy(false);
    if (restoreExportFocus) $("#export").focus({ preventScroll: true });
  }
};
$("#open-export").onclick = async () => {
  try {
    await api("/api/open-export", { batch: exportBatch });
  } catch (error) {
    message(error.message, true);
  }
};
initPreviewCrop();
window.addEventListener("beforeunload", (event) => {
  if (busy || exporting || savePending || saveTimer || saveInFlight || singleCropDirty()) {
    event.preventDefault();
    event.returnValue = "";
  }
});
refresh()
  .then(() =>
    message(
      state.photos.length
        ? "已還原上次工作，勾選照片或點下方配對繼續。"
        : "匯入照片後，勾選兩張開始配對。",
    ),
  )
  .catch((error) =>
    message(`無法連線到本機工具：${error.message}。請重新啟動工具`, true),
  );
