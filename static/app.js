const video = document.getElementById("video");
const canvas = document.getElementById("canvas");
const preview = document.getElementById("preview");
const previewWrap = document.getElementById("previewWrap");
const overlay = document.getElementById("overlay");

const info = document.getElementById("info");
const sourceFile = document.getElementById("sourceFile");
const sourceInput = document.getElementById("sourceInput");
const excelPreview = document.getElementById("excelPreview");
const selectionInfo = document.getElementById("selectionInfo");
const searchInput = document.getElementById("searchInput");
const chkToggleAll = document.getElementById("chkToggleAll");
const statusBanner = document.getElementById("statusBanner");
const statusText = document.getElementById("statusText");
const statusBar = document.getElementById("statusBar");
const serverIndicator = document.getElementById("serverIndicator");
const serverLabel = document.getElementById("serverLabel");
const renderIdleCountdown = document.getElementById("renderIdleCountdown");
const selectionLockNote = document.getElementById("selectionLockNote");
const confirmModal = document.getElementById("confirmModal");
const confirmMessage = document.getElementById("confirmMessage");
const btnCancelConfirm = document.getElementById("btnCancelConfirm");
const btnConfirmApply = document.getElementById("btnConfirmApply");

const turnProgress = document.getElementById("turnProgress");
const nextId = document.getElementById("nextId");
const nextName = document.getElementById("nextName");
const nextFirstName = document.getElementById("nextFirstName");
const nextSecondName = document.getElementById("nextSecondName");
const nextFirstLastName = document.getElementById("nextFirstLastName");
const nextSecondLastName = document.getElementById("nextSecondLastName");
const filtroSelect = document.getElementById("filtro");

const sourceTotal = document.getElementById("sourceTotal");
const targetLabel = document.getElementById("targetLabel");

const bar = document.getElementById("bar");
const txtProgress = document.getElementById("txtProgress");
const txtPercent = document.getElementById("txtPercent");

const btnUploadSource = document.getElementById("btnUploadSource");
const btnResetSession = document.getElementById("btnResetSession");
const btnZipSession = document.getElementById("btnZipSession");
const btnApplySelection = document.getElementById("btnApplySelection");

const btnActivarCamara = document.getElementById("btnActivarCamara");
const btnCapturar = document.getElementById("btnCapturar");
const btnAuto = document.getElementById("btnAuto");
const btnGuardar = document.getElementById("btnGuardar");
const btnVolver = document.getElementById("btnVolver");
const btnUndo = document.getElementById("btnUndo");
const btnLimpiar = document.getElementById("btnLimpiar");

let puntos = [];
let dragIndex = -1;
let imgW = 0;
let imgH = 0;
let cameraReady = false;
let excelRecords = [];
let searchQuery = "";
let pendingApplySelection = null;
let draftSelection = new Set();
let draftSelectionInitialized = false;
let lastSourceFile = "";
let statusTimer = null;
let statusBarTimer = null;
let idleCountdownTimer = null;
let lastUserActivityAt = Date.now();
let lastServerPingAt = 0;
const renderIdleSeconds = Math.max(0, Number(document.body.dataset.renderIdleSeconds || 900));
const minPingGapMs = 15000;
let state = {
  current: 0,
  total: 0,
  source_total: 0,
  target: 0,
  next_id: "",
  next_name: "",
  next_first_name: "",
  next_second_name: "",
  next_first_last_name: "",
  next_second_last_name: "",
  source_file: "",
  has_records: false,
};

function setMsg(msg, ok = true) {
  info.style.color = ok ? "#198754" : "#d64545";
  info.textContent = msg || "";
}

function showStatus(msg, kind = "info", duration = 2200) {
  if (statusTimer) window.clearTimeout(statusTimer);
  if (statusBarTimer) window.clearTimeout(statusBarTimer);

  statusBanner.classList.remove("success", "error", "info");
  statusBanner.classList.add(kind);
  statusText.textContent = msg;
  statusBar.style.width = "100%";

  window.requestAnimationFrame(() => {
    statusBar.style.width = "0%";
  });

  statusTimer = window.setTimeout(() => {
    statusBanner.classList.remove("success", "error", "info");
    statusText.textContent = "Listo.";
    statusBar.style.width = "100%";
  }, duration);
}

function setServerOnline(online) {
  if (!serverIndicator || !serverLabel) return;

  serverIndicator.classList.remove("online", "offline");
  if (online) {
    serverIndicator.classList.add("online");
    serverLabel.textContent = "Servidor en linea";
  } else {
    serverIndicator.classList.add("offline");
    serverLabel.textContent = "Servidor desconectado";
  }
}

function openConfirmModal(message, onConfirm) {
  pendingApplySelection = onConfirm;
  confirmMessage.textContent = message;
  confirmModal.classList.add("open");
  confirmModal.setAttribute("aria-hidden", "false");
}

function closeConfirmModal() {
  pendingApplySelection = null;
  confirmModal.classList.remove("open");
  confirmModal.setAttribute("aria-hidden", "true");
}

function normalizeText(value) {
  return String(value || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .trim();
}

function syncDraftSelectionFromState() {
  draftSelection = new Set(Array.isArray(state.selected_indices) ? state.selected_indices : []);
  draftSelectionInitialized = true;
}

function beep() {
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = "triangle";
  osc.frequency.setValueAtTime(1200, ctx.currentTime);
  gain.gain.setValueAtTime(0.001, ctx.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.16, ctx.currentTime + 0.01);
  gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.12);
  osc.connect(gain);
  gain.connect(ctx.destination);
  osc.start();
  osc.stop(ctx.currentTime + 0.14);
}

function fmt(value) {
  if (!value) return "-";
  return String(value).trim() || "-";
}

function applyState(data) {
  if (!data || typeof data !== "object") return;
  state = { ...state, ...data };

  const sourceChanged = state.source_file !== lastSourceFile;
  if (sourceChanged) {
    lastSourceFile = state.source_file || "";
    syncDraftSelectionFromState();
  }

  if (state.selection_locked) {
    syncDraftSelectionFromState();
  }

  const percent = state.total > 0 ? Math.round((state.current / state.total) * 100) : 0;
  if (bar) bar.style.width = `${percent}%`;
  if (txtProgress) txtProgress.textContent = `Progreso: ${state.current}/${state.total}`;
  if (txtPercent) txtPercent.textContent = `${percent}%`;

  if (turnProgress) turnProgress.textContent = `${state.current}/${state.total}`;
  if (sourceTotal) sourceTotal.textContent = state.source_total;
  if (targetLabel) targetLabel.textContent = state.target;

  if (sourceFile) sourceFile.textContent = state.source_file || "Sin archivo";

  if (nextId) nextId.textContent = fmt(state.next_id);
  if (nextName) nextName.textContent = fmt(state.next_name);
  if (nextFirstName) nextFirstName.textContent = fmt(state.next_first_name);
  if (nextSecondName) nextSecondName.textContent = fmt(state.next_second_name);
  if (nextFirstLastName) nextFirstLastName.textContent = fmt(state.next_first_last_name);
  if (nextSecondLastName) nextSecondLastName.textContent = fmt(state.next_second_last_name);

  if (state.selected_total !== undefined) {
    selectionInfo.textContent = `${state.selected_total} seleccionadas`;
  }

  if (selectionLockNote) {
    selectionLockNote.textContent = state.selection_locked
      ? "La seleccion esta bloqueada. Carga otra caracterizacion para cambiarla."
      : "Selecciona personas y aplica la seleccion antes de capturar.";
  }

  const canScanFlow = !!state.has_records && !!state.selection_locked && state.total > 0;
  const blocked = !canScanFlow || state.current >= state.total;

  if (btnCapturar) btnCapturar.disabled = blocked || !cameraReady;
  if (btnGuardar) btnGuardar.disabled = blocked;
  if (btnAuto) btnAuto.disabled = blocked || !cameraReady;
  if (btnVolver) btnVolver.disabled = !canScanFlow;
  if (btnUndo) btnUndo.disabled = !canScanFlow || state.current <= 0;
  if (btnLimpiar) btnLimpiar.disabled = !canScanFlow;
  if (filtroSelect) filtroSelect.disabled = !canScanFlow;
  if (btnApplySelection) btnApplySelection.disabled = !state.has_records || state.selection_locked;
  if (searchInput) searchInput.disabled = !state.has_records;
  if (chkToggleAll) chkToggleAll.disabled = !state.has_records || state.selection_locked;

  if (blocked && state.has_records && state.total > 0 && state.current >= state.total) {
    setMsg("Meta alcanzada. No se permiten mas escaneos.", false);
  }

  if (excelRecords.length) {
    renderExcelPreview(excelRecords);
  }
}

async function readJson(response) {
  const data = await response.json();
  applyState(data);
  return data;
}

async function refreshStatus() {
  try {
    const response = await fetch("/status");
    const data = await readJson(response);
    setServerOnline(true);
    const startupMessage = document.body.dataset.startupMessage || "";
    if (startupMessage && !state.has_records) {
      setMsg(startupMessage, false);
    } else if (!startupMessage && data && data.has_records && state.current < state.total) {
      setMsg("Listo para escanear.", true);
    }
  } catch (err) {
    setServerOnline(false);
    setMsg("No se pudo obtener estado del servidor.", false);
  }
}

async function heartbeatStatus() {
  try {
    const response = await fetch("/status", { cache: "no-store" });
    if (!response.ok) throw new Error("Status no disponible");
    setServerOnline(true);
  } catch (_err) {
    setServerOnline(false);
  }
}

function formatCountdown(totalSeconds) {
  const seconds = Math.max(0, totalSeconds);
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function updateIdleCountdown() {
  if (!renderIdleCountdown) return;
  if (renderIdleSeconds <= 0) {
    renderIdleCountdown.textContent = "Inactividad: sin suspension";
    return;
  }

  const elapsed = Math.floor((Date.now() - lastUserActivityAt) / 1000);
  const remaining = Math.max(0, renderIdleSeconds - elapsed);

  if (remaining > 0) {
    renderIdleCountdown.textContent = `Inactividad: ${formatCountdown(remaining)}`;
  } else {
    renderIdleCountdown.textContent = "Inactividad: posible suspension";
  }
}

function registerUserActivity() {
  lastUserActivityAt = Date.now();
  updateIdleCountdown();

  const now = Date.now();
  if (now - lastServerPingAt < minPingGapMs) return;
  lastServerPingAt = now;
  heartbeatStatus();
}

function setupIdleTracker() {
  updateIdleCountdown();
  if (idleCountdownTimer) {
    window.clearInterval(idleCountdownTimer);
  }
  idleCountdownTimer = window.setInterval(updateIdleCountdown, 1000);

  const events = ["pointerdown", "keydown", "touchstart", "click", "input"];
  events.forEach((eventName) => {
    window.addEventListener(eventName, registerUserActivity, { passive: true });
  });
}

function renderExcelPreview(records) {
  excelRecords = Array.isArray(records) ? records : [];
  if (!excelPreview) return;

  if (!excelRecords.length) {
    excelPreview.innerHTML = '<div style="padding:12px;color:#5a6880;">Sube el Excel de caracterizacion para ver y seleccionar personas.</div>';
    if (selectionInfo) selectionInfo.textContent = "0 seleccionadas";
    return;
  }

  if (!state.selection_locked && !draftSelectionInitialized) {
    syncDraftSelectionFromState();
  }

  const selectedSet = state.selection_locked
    ? new Set(Array.isArray(state.selected_indices) ? state.selected_indices : [])
    : new Set(draftSelection);
  const scannedSet = new Set(Array.isArray(state.scanned_ids) ? state.scanned_ids : []);
  const locked = !!state.selection_locked;
  const query = normalizeText(searchQuery);

  const rows = excelRecords
    .filter((record) => {
      if (!query) return true;
      const haystack = normalizeText(`${record.id || ""} ${record.full_name || ""} ${record.first_name || ""} ${record.second_name || ""} ${record.first_last_name || ""} ${record.second_last_name || ""}`);
      return haystack.includes(query);
    })
    .map((record) => {
      const name = record.full_name || "";
      const id = record.id || "";
      const isSelected = selectedSet.has(record.index);
      const isScanned = scannedSet.has(id);
      return `
        <tr data-index="${record.index}" class="${isSelected ? "selected" : ""} ${isScanned ? "scanned" : ""}">
          <td>
            <span class="row-status ${isScanned ? "done" : "pending"}">${isScanned ? "✓" : "•"}</span>
          </td>
          <td><input class="row-check" type="checkbox" data-index="${record.index}" ${isSelected ? "checked" : ""} ${locked ? "disabled" : ""} /></td>
          <td>${record.index + 1}</td>
          <td>${id}</td>
          <td>${name}</td>
          <td>${isScanned ? `<a class="btn soft" style="padding:4px 8px;font-size:0.8rem;" href="/pdf/${encodeURIComponent(id)}" target="_blank" rel="noopener noreferrer">Ver PDF</a>` : '<span style="color:#7d8ca5;">-</span>'}</td>
        </tr>
      `;
    })
    .join("");

  excelPreview.innerHTML = `
    <table class="excel-table">
      <thead>
        <tr>
          <th>Estado</th>
          <th>Usar</th>
          <th>#</th>
          <th>ID</th>
          <th>Nombre</th>
          <th>PDF</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;

  const checks = excelPreview.querySelectorAll(".row-check");
  checks.forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      if (state.selection_locked) return;
      const index = Number(checkbox.dataset.index);
      if (checkbox.checked) {
        draftSelection.add(index);
      } else {
        draftSelection.delete(index);
      }

      const selectedCount = draftSelection.size;
      if (selectionInfo) selectionInfo.textContent = `${selectedCount} seleccionadas`;
      const row = checkbox.closest("tr");
      if (row) row.classList.toggle("selected", checkbox.checked);
      updateToggleAllCheck();
    });
    const row = checkbox.closest("tr");
    if (row && checkbox.checked) row.classList.add("selected");
  });

  const visibleCount = rows ? rows.length : 0;
  if (query) {
    if (selectionInfo) selectionInfo.textContent = `${selectedSet.size} seleccionadas | ${visibleCount} visibles`;
  } else if (selectionInfo) {
    selectionInfo.textContent = `${selectedSet.size} seleccionadas`;
  }

  updateToggleAllCheck();
}

async function iniciarCamara() {
  const isLocalhost = ["localhost", "127.0.0.1"].includes(window.location.hostname);
  const isSecure = window.isSecureContext;

  if (!isSecure && !isLocalhost) {
    setMsg(
      "Contexto no seguro para camara en esta URL. En PC usa http://127.0.0.1:5000. En celular usa HTTPS (tunel) o localhost via depuracion.",
      false
    );
    cameraReady = false;
    applyState(state);
    return;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setMsg("Este navegador no soporta acceso a camara.", false);
    return;
  }

  const constraintsList = [
    {
      video: {
        facingMode: { ideal: "environment" },
        width: { ideal: 1920 },
        height: { ideal: 1080 },
      },
    },
    { video: { facingMode: "environment" } },
    { video: true },
  ];

  try {
    const currentStream = video.srcObject;
    if (currentStream && currentStream.getTracks) {
      for (const track of currentStream.getTracks()) {
        track.stop();
      }
    }

    let stream = null;
    let lastError = null;

    for (const constraints of constraintsList) {
      try {
        stream = await navigator.mediaDevices.getUserMedia(constraints);
        if (stream) break;
      } catch (err) {
        lastError = err;
      }
    }

    if (!stream) {
      throw lastError || new Error("No se pudo obtener stream de camara");
    }

    video.setAttribute("playsinline", "true");
    video.muted = true;
    video.srcObject = stream;
    await video.play();
    cameraReady = true;
    applyState(state);
    setMsg("Camara lista.", true);
    showStatus("Camara activada", "success");
  } catch (err) {
    cameraReady = false;
    applyState(state);
    const reason = err && err.name ? err.name : "Error desconocido";
    if (reason === "NotAllowedError") {
      setMsg("Permiso de camara denegado. Habilitalo en el navegador y recarga.", false);
      showStatus("Permiso de camara denegado", "error");
      return;
    }
    if (reason === "NotFoundError") {
      setMsg("No se encontro camara disponible en este dispositivo.", false);
      showStatus("No se encontro camara", "error");
      return;
    }
    setMsg(`No se pudo acceder a la camara (${reason}).`, false);
    showStatus("Error accediendo a la camara", "error");
  }
}

async function cargarVistaPrevia() {
  const response = await fetch("/preview-source");
  const data = await readJson(response);
  if (data.ok && data.records) {
    renderExcelPreview(data.records);
    showStatus("Excel cargado y listo para seleccionar", "success");
  } else {
    excelPreview.innerHTML = '<div style="padding:12px;color:#5a6880;">Sube el Excel de caracterizacion para ver y seleccionar personas.</div>';
  }
}

function updateVisibleSelection(checked) {
  if (state.selection_locked) return;
  if (!excelPreview) return;

  const checks = Array.from(excelPreview.querySelectorAll(".row-check"));
  checks.forEach((checkbox) => {
    const row = checkbox.closest("tr");
    if (!row || row.classList.contains("hidden-row")) return;
    checkbox.checked = checked;
    row.classList.toggle("selected", checked);
    const index = Number(checkbox.dataset.index);
    if (checked) {
      draftSelection.add(index);
    } else {
      draftSelection.delete(index);
    }
  });

  const selectedCount = draftSelection.size;
  if (selectionInfo) selectionInfo.textContent = `${selectedCount} seleccionadas`;
  updateToggleAllCheck();
}

function updateToggleAllCheck() {
  if (!chkToggleAll || !excelPreview) return;

  const visibleChecks = Array.from(excelPreview.querySelectorAll(".row-check"));
  if (!visibleChecks.length) {
    chkToggleAll.checked = false;
    chkToggleAll.indeterminate = false;
    return;
  }

  const checkedCount = visibleChecks.filter((checkbox) => checkbox.checked).length;
  if (checkedCount === 0) {
    chkToggleAll.checked = false;
    chkToggleAll.indeterminate = false;
  } else if (checkedCount === visibleChecks.length) {
    chkToggleAll.checked = true;
    chkToggleAll.indeterminate = false;
  } else {
    chkToggleAll.checked = false;
    chkToggleAll.indeterminate = true;
  }
}

function puntosDefault() {
  const m = 30;
  return [
    { x: m, y: m },
    { x: imgW - m, y: m },
    { x: imgW - m, y: imgH - m },
    { x: m, y: imgH - m },
  ];
}

function dibujarOverlay() {
  overlay.width = preview.clientWidth;
  overlay.height = preview.clientHeight;
  const ctx = overlay.getContext("2d");
  ctx.clearRect(0, 0, overlay.width, overlay.height);

  const sx = overlay.width / imgW;
  const sy = overlay.height / imgH;
  const p = puntos.map((pt) => ({ x: pt.x * sx, y: pt.y * sy }));

  ctx.lineWidth = 3;
  ctx.strokeStyle = "#25d366";
  ctx.beginPath();
  ctx.moveTo(p[0].x, p[0].y);
  for (let i = 1; i < 4; i++) ctx.lineTo(p[i].x, p[i].y);
  ctx.closePath();
  ctx.stroke();

  for (let i = 0; i < p.length; i++) {
    ctx.beginPath();
    ctx.fillStyle = "#ffffff";
    ctx.strokeStyle = "#1f6feb";
    ctx.lineWidth = 3;
    ctx.arc(p[i].x, p[i].y, 10, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
}

function eventoPos(e) {
  const r = overlay.getBoundingClientRect();
  const touch = e.touches ? e.touches[0] : e;
  const x = touch.clientX - r.left;
  const y = touch.clientY - r.top;
  const sx = imgW / overlay.width;
  const sy = imgH / overlay.height;
  return { x: x * sx, y: y * sy, drawX: x, drawY: y };
}

function hitCorner(drawX, drawY) {
  const sx = overlay.width / imgW;
  const sy = overlay.height / imgH;
  for (let i = 0; i < puntos.length; i++) {
    const px = puntos[i].x * sx;
    const py = puntos[i].y * sy;
    if (Math.hypot(px - drawX, py - drawY) < 20) return i;
  }
  return -1;
}

function iniciarDrag(e) {
  if (!puntos.length) return;
  const pos = eventoPos(e);
  dragIndex = hitCorner(pos.drawX, pos.drawY);
  if (dragIndex >= 0) e.preventDefault();
}

function moverDrag(e) {
  if (dragIndex < 0) return;
  const pos = eventoPos(e);
  puntos[dragIndex].x = Math.max(0, Math.min(imgW, pos.x));
  puntos[dragIndex].y = Math.max(0, Math.min(imgH, pos.y));
  dibujarOverlay();
  e.preventDefault();
}

function terminarDrag() {
  dragIndex = -1;
}

function volverCamara() {
  preview.src = "";
  puntos = [];
  previewWrap.style.display = "none";
  video.style.display = "block";
}

function limpiarVista() {
  volverCamara();
  setMsg("Vista limpiada.", true);
}

function capturar() {
  if (!cameraReady) {
    setMsg("Primero pulsa Activar camara y acepta el permiso.", false);
    return;
  }

  if (!state.has_records || state.current >= state.total) {
    setMsg("No puedes capturar, la meta esta completa o no hay fuente cargada.", false);
    return;
  }

  const ctx = canvas.getContext("2d");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  ctx.drawImage(video, 0, 0);

  beep();

  preview.src = canvas.toDataURL("image/jpeg", 0.95);
  imgW = canvas.width;
  imgH = canvas.height;
  puntos = puntosDefault();

  video.style.display = "none";
  previewWrap.style.display = "block";
  setTimeout(() => dibujarOverlay(), 30);
  setMsg("Ajusta las esquinas y guarda.", true);
  showStatus("Captura realizada", "success");
}

async function usarAuto() {
  if (!preview.src) {
    setMsg("Primero captura una imagen.", false);
    return;
  }

  const response = await fetch("/auto-corners", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ img: preview.src }),
  });
  const data = await readJson(response);

  if (data.ok && data.corners && data.corners.length === 4) {
    puntos = data.corners.map((p) => ({ x: p[0], y: p[1] }));
    dibujarOverlay();
    setMsg("Esquinas detectadas automaticamente.", true);
    showStatus("Esquinas detectadas", "success");
  } else {
    setMsg(data.msg || "No se detectaron esquinas.", false);
    showStatus(data.msg || "No se detectaron esquinas", "error");
  }
}

async function enviar() {
  if (!preview.src) {
    setMsg("No hay captura para guardar.", false);
    return;
  }
  if (puntos.length !== 4) {
    setMsg("Debes ajustar 4 esquinas.", false);
    return;
  }

  const filtro = document.getElementById("filtro").value;
  const payload = {
    img: preview.src,
    corners: puntos.map((p) => [p.x, p.y]),
    filter: filtro,
  };

  const response = await fetch("/upload", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await readJson(response);
  setMsg(data.msg || "Operacion completada.", !!data.ok);
  showStatus(data.msg || "Operacion completada", data.ok ? "success" : "error");

  if (data.ok) {
    volverCamara();
  }
}

async function deshacerUltimo() {
  const response = await fetch("/undo-last", { method: "POST" });
  const data = await readJson(response);
  setMsg(data.msg || "Operacion completada.", !!data.ok);
  showStatus(data.msg || "Operacion completada", data.ok ? "success" : "error");
}

async function subirFuente() {
  const file = sourceInput.files[0];
  if (!file) {
    setMsg("Selecciona un archivo de caracterizacion Excel.", false);
    return;
  }

  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch("/load-source", {
    method: "POST",
    body: formData,
  });
  const data = await readJson(response);
  setMsg(data.msg || "Fuente cargada.", !!data.ok);
  showStatus(data.msg || "Fuente cargada", data.ok ? "success" : "error");

  if (data.ok) {
    searchQuery = "";
    if (searchInput) searchInput.value = "";
    syncDraftSelectionFromState();
    await cargarVistaPrevia();
    volverCamara();
  }
}

async function aplicarSeleccion() {
  const selectedIndices = Array.from(draftSelection);

  if (!selectedIndices.length) {
    setMsg("Selecciona al menos una persona.", false);
    return;
  }

  openConfirmModal(
    `Vas a bloquear la seleccion de ${selectedIndices.length} personas. Al confirmar ya no se podra editar hasta cargar otra caracterizacion.`,
    async () => {
      const response = await fetch("/set-selection", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selected_indices: selectedIndices }),
      });
      const data = await readJson(response);
      setMsg(data.msg || "Seleccion aplicada.", !!data.ok);
      showStatus(data.msg || "Seleccion aplicada", data.ok ? "success" : "error");

      if (data.ok) {
        btnApplySelection.disabled = true;
        chkToggleAll.disabled = true;
        const checks = excelPreview.querySelectorAll(".row-check");
        checks.forEach((checkbox) => {
          checkbox.disabled = true;
        });
        renderExcelPreview(excelRecords);
      }
      closeConfirmModal();
    }
  );
}

async function reiniciarJornada() {
  const ok = window.confirm("Se reiniciara contador e historial. Deseas continuar?");
  if (!ok) return;

  const response = await fetch("/reset-session", { method: "POST" });
  const data = await readJson(response);
  setMsg(data.msg || "Jornada reiniciada.", !!data.ok);
  showStatus(data.msg || "Jornada reiniciada", data.ok ? "success" : "error");
  volverCamara();
}

function descargarZip(scope) {
  const target = scope === "all" ? "all" : "session";
  window.open(`/download-zip?scope=${target}`, "_blank");
}

function handleSearch() {
  if (!searchInput) return;
  searchQuery = searchInput.value || "";
  renderExcelPreview(excelRecords);
}

function bindIfExists(element, eventName, handler) {
  if (element) {
    element.addEventListener(eventName, handler);
  }
}

overlay.addEventListener("mousedown", iniciarDrag);
overlay.addEventListener("mousemove", moverDrag);
window.addEventListener("mouseup", terminarDrag);
overlay.addEventListener("touchstart", iniciarDrag, { passive: false });
overlay.addEventListener("touchmove", moverDrag, { passive: false });
overlay.addEventListener("touchend", terminarDrag);
window.addEventListener("resize", () => {
  if (previewWrap.style.display !== "none" && puntos.length) dibujarOverlay();
});

bindIfExists(btnUploadSource, "click", subirFuente);
bindIfExists(btnResetSession, "click", reiniciarJornada);
bindIfExists(btnZipSession, "click", () => descargarZip("session"));
bindIfExists(btnApplySelection, "click", aplicarSeleccion);
bindIfExists(searchInput, "input", handleSearch);
bindIfExists(chkToggleAll, "change", () => updateVisibleSelection(chkToggleAll.checked));
bindIfExists(btnCancelConfirm, "click", closeConfirmModal);
bindIfExists(btnConfirmApply, "click", () => {
  if (typeof pendingApplySelection === "function") {
    pendingApplySelection();
  } else {
    closeConfirmModal();
  }
});
bindIfExists(confirmModal, "click", (event) => {
  if (event.target === confirmModal) closeConfirmModal();
});

bindIfExists(btnActivarCamara, "click", iniciarCamara);
bindIfExists(btnCapturar, "click", capturar);
bindIfExists(btnAuto, "click", usarAuto);
bindIfExists(btnGuardar, "click", enviar);
bindIfExists(btnVolver, "click", volverCamara);
bindIfExists(btnUndo, "click", deshacerUltimo);
bindIfExists(btnLimpiar, "click", limpiarVista);

(async function init() {
  await refreshStatus();
  await heartbeatStatus();
  setupIdleTracker();

  if (excelPreview) {
    excelPreview.innerHTML = '<div style="padding:12px;color:#5a6880;">Sube el Excel de caracterizacion para ver y seleccionar personas.</div>';
  }
  if (searchInput) searchInput.disabled = true;
  if (chkToggleAll) chkToggleAll.disabled = true;
  setMsg("Pulsa Activar camara para iniciar y aceptar permisos.", true);
})();
