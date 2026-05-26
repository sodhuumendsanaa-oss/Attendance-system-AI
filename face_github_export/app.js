/* ════════════════════════════════════════════════════════════
   app.js — FaceGuard Frontend Logic
   ════════════════════════════════════════════════════════════ */

const API_BASE = "http://localhost:5000/api";

// ── State ──────────────────────────────────────────────────
let stream = null;
let scanTimer = null;
let scanInterval = 2000;
let isScanning = false;
let capturedImageData = null;
let modalStream = null;
let liveLogCount = 0;

const video = document.getElementById("video");
const overlay = document.getElementById("overlay");
const ctx = overlay.getContext("2d");

// ── Tab Navigation ─────────────────────────────────────────

// ── Status Indicator ───────────────────────────────────────
function setStatus(state, label) {
  const dot = document.getElementById("status-dot");
  const lbl = document.getElementById("status-label");
  dot.className = "status-dot " + state;
  lbl.textContent = label;
}

// ── Server Health Check ─────────────────────────────────────
async function checkServer() {
  try {
    const res = await fetch(`${API_BASE}/logs?limit=1`);
    if (res.ok) {
      setStatus("online", "Server Online");
    } else {
      setStatus("offline", "Server Error");
    }
  } catch {
    setStatus("offline", "Server Offline");
  }
}

// ── Camera Start / Stop ─────────────────────────────────────
async function startCamera() {
  try {
    // Request camera with relaxed constraints first (more compatible)
    stream = await navigator.mediaDevices.getUserMedia({
      video: true,
      audio: false
    });

    video.srcObject = stream;

    // Wait for video metadata to load before playing
    await new Promise((resolve, reject) => {
      video.onloadedmetadata = () => resolve();
      video.onerror = (e) => reject(e);
      setTimeout(() => resolve(), 3000); // fallback timeout
    });

    try { await video.play(); } catch (playErr) {
      // Chrome sometimes throws on play() if already playing — safe to ignore
      console.warn("[Camera] play() warning:", playErr.message);
    }

    // Hide placeholder
    document.getElementById("camera-placeholder").style.display = "none";

    // Enable controls
    document.getElementById("btn-start-camera").disabled = true;
    document.getElementById("btn-stop-camera").disabled = false;
    document.getElementById("btn-snapshot").disabled = false;

    // Start scan loop after a short delay to ensure video is rendering
    setTimeout(() => startScanLoop(), 500);

    setDetectionText("🔍 Scanning...", false);
    showToast("Camera started", "info");
    console.log(`[Camera] Started: ${video.videoWidth}x${video.videoHeight}`);
  } catch (err) {
    let msg = err.message;
    if (err.name === "NotAllowedError") msg = "Camera permission denied. Please allow camera access in your browser.";
    if (err.name === "NotFoundError")  msg = "No camera found on this device.";
    if (err.name === "NotReadableError") msg = "Camera is in use by another application.";
    showToast(msg, "error");
    console.error("[Camera]", err);
  }
}

function stopCamera() {
  if (stream) {
    stream.getTracks().forEach(t => t.stop());
    stream = null;
  }
  stopScanLoop();
  video.srcObject = null;

  document.getElementById("camera-placeholder").style.display = "";
  document.getElementById("btn-start-camera").disabled = false;
  document.getElementById("btn-stop-camera").disabled = true;
  document.getElementById("btn-snapshot").disabled = true;

  // Clear canvas
  ctx.clearRect(0, 0, overlay.width, overlay.height);

  setDetectionText("Camera stopped", false);
  showToast("Camera stopped", "info");
}

// ── Scan Loop ───────────────────────────────────────────────
function startScanLoop() {
  stopScanLoop();
  scanTimer = setInterval(sendFrameToServer, scanInterval);
  isScanning = true;
}

function stopScanLoop() {
  if (scanTimer) clearInterval(scanTimer);
  scanTimer = null;
  isScanning = false;
}

function updateScanInterval(value) {
  scanInterval = parseInt(value);
  if (isScanning) startScanLoop();
}

async function sendFrameToServer() {
  if (!stream) return;

  // Wait until video has real dimensions
  const vw = video.videoWidth;
  const vh = video.videoHeight;
  if (!vw || !vh) {
    console.warn("[Scan] Video not ready yet, skipping frame");
    return;
  }

  // Draw frame to offscreen canvas, get base64
  const canvas = document.createElement("canvas");
  canvas.width = vw;
  canvas.height = vh;
  const c = canvas.getContext("2d");
  c.drawImage(video, 0, 0, vw, vh);
  const b64 = canvas.toDataURL("image/jpeg", 0.8);

  // Include current session
  const session = document.getElementById("active-session-select")?.value || "Morning";

  try {
    const res = await fetch(`${API_BASE}/recognize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image: b64 })   // session removed — backend uses period time
    });
    const data = await res.json();
    handleRecognitionResult(data, canvas.width, canvas.height);

    // Update period indicator from response
    updatePeriodIndicator(data.active_period);

    // Handle attendance auto-submissions
    if (data.attendance_events && data.attendance_events.length > 0) {
      data.attendance_events.forEach(evt => {
        showAttendanceToast(evt.name, evt.period, evt.status, evt.time);
        incrementAttendanceBadge();
      });
      if (document.getElementById("tab-attendance").classList.contains("active")) {
        loadAttendance();
      }
    }
  } catch (err) {
    console.error("[Scan]", err);
    setStatus("offline", "Server Offline");
  }
}

async function takeManualSnapshot() {
  await sendFrameToServer();
  showToast("Snapshot sent for analysis", "info");
}

// ── Handle Recognition Results ──────────────────────────────
function handleRecognitionResult(data, w, h) {
  if (data.error) {
    console.error("[Recognize]", data.error);
    return;
  }

  setStatus("online", "Server Online");

  // Sync overlay canvas size to video display size
  overlay.width = video.offsetWidth;
  overlay.height = video.offsetHeight;
  ctx.clearRect(0, 0, overlay.width, overlay.height);

  const scaleX = overlay.width / w;
  const scaleY = overlay.height / h;

  const faces = data.faces || [];

  if (faces.length === 0) {
    setDetectionText("🔍 No face detected", false);
    return;
  }

  faces.forEach(face => {
    const { name, confidence, status, bbox } = face;
    const x = bbox.x * scaleX;
    const y = bbox.y * scaleY;
    const bw = bbox.w * scaleX;
    const bh = bbox.h * scaleY;

    // Choose color by status
    const isKnown = status === "recognized";
    const isUncertain = status === "uncertain";
    const color = isKnown ? "#10b981" : isUncertain ? "#f59e0b" : "#ef4444";
    const glowColor = isKnown ? "rgba(16,185,129,0.4)" : isUncertain ? "rgba(245,158,11,0.4)" : "rgba(239,68,68,0.4)";

    // Draw bounding box
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.shadowBlur = 12;
    ctx.shadowColor = glowColor;
    ctx.strokeRect(x, y, bw, bh);

    // Corner accents
    const cs = 12;
    ctx.lineWidth = 3;
    // Top-left
    ctx.beginPath(); ctx.moveTo(x, y + cs); ctx.lineTo(x, y); ctx.lineTo(x + cs, y); ctx.stroke();
    // Top-right
    ctx.beginPath(); ctx.moveTo(x + bw - cs, y); ctx.lineTo(x + bw, y); ctx.lineTo(x + bw, y + cs); ctx.stroke();
    // Bottom-left
    ctx.beginPath(); ctx.moveTo(x, y + bh - cs); ctx.lineTo(x, y + bh); ctx.lineTo(x + cs, y + bh); ctx.stroke();
    // Bottom-right
    ctx.beginPath(); ctx.moveTo(x + bw - cs, y + bh); ctx.lineTo(x + bw, y + bh); ctx.lineTo(x + bw, y + bh - cs); ctx.stroke();
    ctx.restore();

    // Label background
    const label = name + (confidence !== null ? ` (${Math.round(confidence)})` : "");
    ctx.save();
    ctx.font = "bold 12px Inter, sans-serif";
    const tw = ctx.measureText(label).width;
    const lx = x;
    const ly = y - 24 < 0 ? y + bh + 4 : y - 28;

    ctx.fillStyle = color;
    ctx.shadowBlur = 8;
    ctx.shadowColor = glowColor;
    roundRect(ctx, lx, ly, tw + 16, 22, 4);
    ctx.fill();

    ctx.shadowBlur = 0;
    ctx.fillStyle = "#fff";
    ctx.fillText(label, lx + 8, ly + 15);
    ctx.restore();

    // Add to live sidebar
    addLiveLogEntry({ face_name: name, confidence, captured_at: data.timestamp, status, snapshot_b64: null });
  });

  // Detection text
  const names = faces.map(f => f.name).join(", ");
  setDetectionText(`👤 Detected: ${names}`, true);
}

// ── Canvas Helper ───────────────────────────────────────────
function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

// ── Detection Text ──────────────────────────────────────────
function setDetectionText(text, active) {
  document.getElementById("detection-text").textContent = text;
  const ring = document.getElementById("pulse-ring");
  ring.className = "pulse-ring" + (active ? " active" : "");
}

// ── Live Log Sidebar ────────────────────────────────────────
function addLiveLogEntry(log) {
  const list = document.getElementById("live-log-list");
  const empty = list.querySelector(".log-empty");
  if (empty) empty.remove();

  const isKnown = log.face_name && log.face_name !== "Unknown";
  const div = document.createElement("div");
  div.className = `log-entry ${isKnown ? "known" : "unknown"}`;

  const thumbHtml = log.snapshot_b64
    ? `<img class="log-thumb" src="${log.snapshot_b64}" alt="Snapshot" />`
    : `<div class="log-thumb" style="background:var(--color-bg-3);display:flex;align-items:center;justify-content:center;font-size:1.4rem">${isKnown ? "✅" : "❓"}</div>`;

  const confText = log.confidence !== null && log.confidence !== undefined
    ? `Conf: ${Math.round(log.confidence)}`
    : "";

  div.innerHTML = `
    ${thumbHtml}
    <div class="log-details">
      <div class="log-name ${isKnown ? "known-label" : "unknown-label"}">${log.face_name}</div>
      <div class="log-meta">
        <span class="log-time">${formatTime(log.captured_at)}</span>
        ${confText ? `<span class="log-conf">${confText}</span>` : ""}
      </div>
    </div>
  `;

  list.insertBefore(div, list.firstChild);
  liveLogCount++;
  document.getElementById("live-log-count").textContent = `${liveLogCount} event${liveLogCount !== 1 ? "s" : ""}`;

  // Keep only last 30 in sidebar
  while (list.children.length > 30) {
    list.removeChild(list.lastChild);
  }
}

// ── Format Helpers ──────────────────────────────────────────
function formatTime(datetime) {
  if (!datetime) return "—";
  const d = new Date(datetime.replace(" ", "T"));
  if (isNaN(d)) return datetime;
  return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatDateTime(datetime) {
  if (!datetime) return "—";
  const d = new Date(datetime.replace(" ", "T"));
  if (isNaN(d)) return datetime;
  return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

// ── Load Logs Tab ────────────────────────────────────────────
async function loadLogs() {
  const grid = document.getElementById("logs-grid");
  grid.innerHTML = `<div class="logs-loading">Loading logs...</div>`;
  try {
    const res = await fetch(`${API_BASE}/logs?limit=100`);
    const data = await res.json();
    const logs = data.logs || [];

    if (logs.length === 0) {
      grid.innerHTML = `<div class="logs-loading">No detection logs yet.</div>`;
      return;
    }

    grid.innerHTML = "";
    logs.forEach((log, i) => {
      const isKnown = log.face_name && log.face_name !== "Unknown";
      const card = document.createElement("div");
      card.className = "log-card";
      card.style.animationDelay = `${i * 0.03}s`;

      const imgHtml = log.snapshot_b64
        ? `<img class="log-card-img" src="${log.snapshot_b64}" alt="Detection at ${log.captured_at}" />`
        : `<div class="log-card-img-placeholder">No image</div>`;

      const confText = log.confidence !== null && log.confidence !== undefined
        ? `Confidence: ${Math.round(log.confidence)}`
        : "";

      card.innerHTML = `
        ${imgHtml}
        <div class="log-card-body">
          <div class="log-card-name ${isKnown ? "known-label" : "unknown-label"}">${log.face_name}</div>
          <div class="log-card-meta">
            <span class="log-card-time">🕐 ${formatDateTime(log.captured_at)}</span>
            ${confText ? `<span class="log-card-conf">${confText}</span>` : ""}
          </div>
        </div>
      `;
      grid.appendChild(card);
    });
  } catch (err) {
    grid.innerHTML = `<div class="logs-loading">Error loading logs. Is the server running?</div>`;
    console.error(err);
  }
}

// ── Clear Logs ───────────────────────────────────────────────
async function clearLogs() {
  if (!confirm("Clear all detection logs? This cannot be undone.")) return;
  try {
    await fetch(`${API_BASE}/logs`, { method: "DELETE" });
    showToast("All logs cleared", "success");
    loadLogs();
  } catch (err) {
    showToast("Failed to clear logs", "error");
  }
}

// ── Load Faces Tab ────────────────────────────────────────────
async function loadFaces() {
  const grid = document.getElementById("faces-grid");
  grid.innerHTML = `<div class="faces-loading">Loading registered faces...</div>`;
  try {
    const res = await fetch(`${API_BASE}/faces`);
    const data = await res.json();
    const faces = data.faces || [];

    if (faces.length === 0) {
      grid.innerHTML = `<div class="faces-loading">No faces registered yet. Use "Register Face" to add people.</div>`;
      return;
    }

    grid.innerHTML = "";
    faces.forEach((face, i) => {
      const card = document.createElement("div");
      card.className = "face-card";
      card.style.animationDelay = `${i * 0.05}s`;

      const thumbHtml = face.thumbnail_b64
        ? `<img class="face-card-thumb" src="${face.thumbnail_b64}" alt="${face.name}" />`
        : `<div class="face-card-thumb-placeholder"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/></svg></div>`;

      card.innerHTML = `
        ${thumbHtml}
        <div class="face-card-body">
          <div class="face-card-name">${face.name}</div>
          <div class="face-card-date">Added ${formatDateTime(face.created_at)}</div>
          ${face.commute_line ? `<div class="face-card-date">🚉 ${face.commute_line}</div>` : ""}
          <div class="face-card-actions">
            <button class="btn btn-danger btn-sm" onclick="deleteFace(${face.label_id}, '${face.name}')">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
              Remove
            </button>
          </div>
        </div>
      `;
      grid.appendChild(card);
    });
  } catch (err) {
    grid.innerHTML = `<div class="faces-loading">Error loading faces. Is the server running?</div>`;
    console.error(err);
  }
}

// ── Delete Face ───────────────────────────────────────────────
async function deleteFace(labelId, name) {
  if (!confirm(`Remove "${name}" from recognized faces?`)) return;
  try {
    const res = await fetch(`${API_BASE}/faces/${labelId}`, { method: "DELETE" });
    const data = await res.json();
    if (data.success) {
      showToast(`"${name}" removed`, "success");
      loadFaces();
    }
  } catch (err) {
    showToast("Failed to delete face", "error");
  }
}

// ── Register Modal ────────────────────────────────────────────
async function openRegisterModal() {
  const modal = document.getElementById("modal-register");
  modal.classList.add("open");
  document.getElementById("modal-message").className = "modal-message";
  document.getElementById("modal-message").textContent = "";
  document.getElementById("register-name").value = "";
  document.getElementById("btn-save-face").disabled = true;
  capturedImageData = null;
  document.getElementById("modal-capture-preview").style.display = "none";

  // Start modal camera (use existing stream if available, or open new)
  try {
    if (stream && stream.active) {
      document.getElementById("modal-video").srcObject = stream;
      await document.getElementById("modal-video").play();
    } else {
      modalStream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" },
        audio: false
      });
      document.getElementById("modal-video").srcObject = modalStream;
      await document.getElementById("modal-video").play();
    }
  } catch (err) {
    showModalMessage("Could not access camera: " + err.message, "error");
  }
}

function closeRegisterModal() {
  document.getElementById("modal-register").classList.remove("open");
  if (modalStream) {
    modalStream.getTracks().forEach(t => t.stop());
    modalStream = null;
  }
  document.getElementById("modal-video").srcObject = null;
}

function captureForRegister() {
  const mv = document.getElementById("modal-video");
  if (!mv.srcObject) { showModalMessage("Camera not active", "error"); return; }

  const canvas = document.createElement("canvas");
  canvas.width = mv.videoWidth || 640;
  canvas.height = mv.videoHeight || 480;
  const c = canvas.getContext("2d");
  c.drawImage(mv, 0, 0);
  capturedImageData = canvas.toDataURL("image/jpeg", 0.92);

  // Show preview
  document.getElementById("modal-preview-img").src = capturedImageData;
  document.getElementById("modal-capture-preview").style.display = "";
  document.getElementById("btn-save-face").disabled = false;

  showModalMessage("Photo captured! Enter a name and click Save.", "info");
}

async function saveRegisteredFace() {
  const name = document.getElementById("register-name").value.trim();
  const commuteLine = document.getElementById("register-commute").value.trim();
  if (!name) { showModalMessage("Please enter a name.", "error"); return; }
  if (!capturedImageData) { showModalMessage("Please capture a photo first.", "error"); return; }

  document.getElementById("btn-save-face").disabled = true;
  showModalMessage("Registering face and training model...", "info");

  try {
    const res = await fetch(`${API_BASE}/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, image: capturedImageData, commute_line: commuteLine })
    });
    const data = await res.json();

    if (data.success) {
      showModalMessage(data.message, "success");
      showToast(data.message, "success");
      setTimeout(() => {
        closeRegisterModal();
        loadFaces();
      }, 1500);
    } else {
      showModalMessage(data.error || "Registration failed", "error");
      document.getElementById("btn-save-face").disabled = false;
    }
  } catch (err) {
    showModalMessage("Server error: " + err.message, "error");
    document.getElementById("btn-save-face").disabled = false;
  }
}

function showModalMessage(msg, type) {
  const el = document.getElementById("modal-message");
  el.textContent = msg;
  el.className = "modal-message " + type;
}

// ── Toast Notifications ───────────────────────────────────────
function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateX(20px)";
    toast.style.transition = "all 0.3s ease";
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}

// ── Close modal on overlay click ─────────────────────────────
document.getElementById("modal-register").addEventListener("click", function (e) {
  if (e.target === this) closeRegisterModal();
});

// ── Keyboard shortcuts ────────────────────────────────────────
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeRegisterModal();
  if (e.key === "s" && e.ctrlKey) { e.preventDefault(); takeManualSnapshot(); }
});

// ════════════════════════════════════════════════════════════
// ATTENDANCE SYSTEM
// ════════════════════════════════════════════════════════════

let attendanceBadgeCount = 0;

// ── Period Indicator ──────────────────────────────────────────────
function updatePeriodIndicator(activePeriod) {
  const el = document.getElementById("period-indicator");
  const lbl = document.getElementById("period-label");
  if (!el || !lbl) return;

  if (!activePeriod) {
    el.className = "period-indicator";
    lbl.textContent = "No active period";
    return;
  }

  const statusClass = activePeriod.status === "Present" ? "present" : "late";
  el.className = `period-indicator ${statusClass}`;
  const icon = activePeriod.status === "Present" ? "✅" : "🟡";
  lbl.textContent = `${icon} ${activePeriod.name} — ${activePeriod.status} (until ${activePeriod.window_end})`;
}

// Poll periods API every 30s to keep indicator current
async function pollPeriods() {
  try {
    const res = await fetch(`${API_BASE}/periods`);
    const data = await res.json();
    const ap = data.active_period
      ? { name: data.active_period, status: data.active_status, window_end: "" }
      : null;
    updatePeriodIndicator(ap);

    // Also update period filter options dynamically
    const sel = document.getElementById("att-session-select");
    if (sel && data.periods) {
      // Keep "All Periods" option, rebuild the rest
      const current = sel.value;
      while (sel.options.length > 1) sel.remove(1);
      data.periods.forEach(p => {
        const opt = new Option(p.name, p.name);
        sel.add(opt);
      });
      sel.value = current;
    }
  } catch (e) { /* silent */ }
}

// ── Attendance Tab Nav ────────────────────────────────────────
function switchTab(tab) {
  document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
  document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
  document.getElementById(`tab-${tab}`).classList.add("active");
  document.getElementById(`nav-${tab}`).classList.add("active");

  if (tab === "logs") loadLogs();
  if (tab === "faces") loadFaces();
  if (tab === "attendance") {
    resetAttendanceBadge();
    loadAttendance();
    fetchDelays();
  }
}

// ── Badge Helpers ─────────────────────────────────────────────
function incrementAttendanceBadge() {
  attendanceBadgeCount++;
  const badge = document.getElementById("attendance-badge");
  if (badge) {
    badge.style.display = "";
    badge.textContent = attendanceBadgeCount;
    // Re-trigger animation
    badge.style.animation = "none";
    requestAnimationFrame(() => { badge.style.animation = ""; });
  }
}

function resetAttendanceBadge() {
  attendanceBadgeCount = 0;
  const badge = document.getElementById("attendance-badge");
  if (badge) badge.style.display = "none";
}

// ── Attendance Toast ──────────────────────────────────────────
function showAttendanceToast(name, period, status, time) {
  const container = document.getElementById("toast-container");
  const toast = document.createElement("div");
  const icon = status === "Present" ? "✅" : "🟡";
  const color = status === "Present" ? "success" : "info";
  toast.className = `toast ${color}`;
  toast.innerHTML = `${icon} <strong>${name}</strong> — 出席登録完了！<br><small style="opacity:0.7">${period} / ${status} ${time}</small>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateX(20px)";
    toast.style.transition = "all 0.3s ease";
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// ── Load Attendance Tab ───────────────────────────────────────
async function loadAttendance() {
  const datePicker = document.getElementById("att-date-picker");
  const date = datePicker.value || new Date().toISOString().split("T")[0];
  const session = document.getElementById("att-session-select")?.value || "";

  // Update header date label
  document.getElementById("attendance-date-label").textContent = date;

  const params = new URLSearchParams({ date });
  if (session) params.append("session", session);

  try {
    const res = await fetch(`${API_BASE}/attendance?${params}`);
    const data = await res.json();
    renderAttendanceStats(data.stats);
    renderAttendanceTable(data.records);
  } catch (err) {
    document.getElementById("att-tbody").innerHTML =
      `<tr><td colspan="7" class="att-empty">Error loading attendance. Is the server running?</td></tr>`;
    console.error(err);
  }
}

// ── Render Stats ──────────────────────────────────────────────
function renderAttendanceStats(stats) {
  if (!stats) return;
  document.getElementById("stat-present").textContent = stats.present ?? 0;
  document.getElementById("stat-late").textContent    = stats.late ?? 0;
  document.getElementById("stat-absent").textContent  = stats.absent ?? 0;
  document.getElementById("stat-total").textContent   = stats.total_registered ?? 0;
  const rate = stats.total_registered > 0
    ? Math.round(((stats.present + stats.late) / stats.total_registered) * 100) + "%"
    : "—";
  document.getElementById("stat-rate").textContent = rate;
}

// ── Render Table ──────────────────────────────────────────────
async function fetchDelays() {
  const listEl = document.getElementById("delays-list");
  if (!listEl) return;
  
  listEl.innerHTML = "情報を取得中... (Fetching...)";
  
  try {
    const res = await fetch(`${API_BASE}/delays`);
    const data = await res.json();
    
    if (data.success) {
      if (data.delayed_lines && data.delayed_lines.length > 0) {
        listEl.innerHTML = data.delayed_lines.map(line => 
          `<span style="background: var(--color-danger); color: white; padding: 2px 8px; border-radius: 12px; font-weight: bold;">${line}</span>`
        ).join("");
      } else {
        listEl.innerHTML = "現在、遅延している路線はありません (No delays reported).";
      }
    } else {
      listEl.innerHTML = `取得エラー: ${data.error}`;
    }
  } catch (err) {
    listEl.innerHTML = "情報の取得に失敗しました。";
  }
}

function renderAttendanceTable(records) {
  const tbody = document.getElementById("att-tbody");
  if (!records || records.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="att-empty">No attendance records for this date / session.</td></tr>`;
    return;
  }

  tbody.innerHTML = "";
  records.forEach((rec, i) => {
    const statusClass = rec.status.toLowerCase();
    const statusIcon = rec.status === "Present" ? "✅" : rec.status === "Late" ? "🟡" : "❌";

    const thumbHtml = rec.snapshot_b64
      ? `<img class="att-thumb" src="${rec.snapshot_b64}" alt="${rec.student_name}" />`
      : `<div class="att-thumb-placeholder">👤</div>`;

    const sessionIcon = rec.session === "Morning" ? "☀️" : rec.session === "Afternoon" ? "🌤️" : "🌙";
    
    let excuseHtml = "";
    if (rec.excused_reason) {
      excuseHtml = `<div style="font-size:0.7rem;color:var(--color-text-muted);margin-top:2px;">${rec.excused_reason}</div>`;
    }

    let delayBtnHtml = "";
    if (rec.status === "Late" && rec.commute_line) {
      delayBtnHtml = `
        <button class="btn btn-secondary btn-sm" style="margin-top: 4px; padding: 2px 6px; font-size: 0.7rem;" 
          onclick="openDelayModal(${rec.id}, '${rec.student_name}', '${rec.commute_line}', '${rec.time_in}')">
          🚉 Verify Delay
        </button>
      `;
    }

    const tr = document.createElement("tr");
    tr.style.animationDelay = `${i * 0.04}s`;
    tr.innerHTML = `
      <td style="color:var(--color-text-muted);font-family:var(--font-mono)">${i + 1}</td>
      <td>${thumbHtml}</td>
      <td class="att-name">
        ${rec.student_name}
        ${rec.commute_line ? `<div style="font-size:0.75rem;font-weight:normal;color:var(--color-text-muted)">🚉 ${rec.commute_line}</div>` : ""}
      </td>
      <td><span class="att-session-tag">${sessionIcon} ${rec.session}</span></td>
      <td class="att-time">${rec.time_in}</td>
      <td>
        <span class="status-badge ${statusClass}">${statusIcon} ${rec.status}</span>
        ${excuseHtml}
      </td>
      <td>
        <select class="att-override-select" onchange="overrideAttendance(${rec.id}, this.value)">
          <option value="Present" ${rec.status === "Present" ? "selected" : ""}>✅ Present</option>
          <option value="Late"    ${rec.status === "Late"    ? "selected" : ""}>🟡 Late</option>
          <option value="Absent"  ${rec.status === "Absent"  ? "selected" : ""}>❌ Absent</option>
        </select>
        ${delayBtnHtml}
      </td>
    `;
    tbody.appendChild(tr);
  });
}

// ── Override Status ───────────────────────────────────────────
async function overrideAttendance(id, newStatus, excuse = "") {
  try {
    const res = await fetch(`${API_BASE}/attendance/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: newStatus, excuse: excuse })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Status → ${newStatus}`, "success");
      loadAttendance(); // Refresh table
    }
  } catch (err) {
    showToast("Failed to update status", "error");
  }
}

// ── Export CSV ────────────────────────────────────────────────
function exportAttendanceCSV() {
  const date = document.getElementById("att-date-picker").value ||
               new Date().toISOString().split("T")[0];
  const session = document.getElementById("att-session-select")?.value || "";
  const params = new URLSearchParams({ date });
  if (session) params.append("session", session);
  window.open(`${API_BASE}/attendance/export?${params}`, "_blank");
  showToast("CSV download started", "info");
}

// ── Delay Modal ───────────────────────────────────────────────
let activeDelayRecordId = null;
let activeDelayCommuteLine = "";

function openDelayModal(recordId, studentName, commuteLine, timeIn) {
  activeDelayRecordId = recordId;
  activeDelayCommuteLine = commuteLine;
  document.getElementById("delay-student-name").textContent = studentName;
  document.getElementById("delay-time-in").textContent = timeIn;
  document.getElementById("delay-commute-line").textContent = commuteLine;
  
  document.getElementById("modal-delay").style.display = "flex";
  // Small delay to allow CSS transition if needed
  setTimeout(() => document.getElementById("modal-delay").classList.add("open"), 10);
  
  // Set up the approve button
  document.getElementById("btn-approve-delay").onclick = async function() {
    await overrideAttendance(activeDelayRecordId, "Present", `Train Delay: ${activeDelayCommuteLine}`);
    closeDelayModal();
  };
}

function closeDelayModal() {
  document.getElementById("modal-delay").classList.remove("open");
  setTimeout(() => {
    document.getElementById("modal-delay").style.display = "none";
  }, 200);
}

// ── Init ─────────────────────────────────────────────────────
(async function init() {
  const today = new Date().toISOString().split("T")[0];
  const dp = document.getElementById("att-date-picker");
  if (dp) { dp.value = today; dp.max = today; }

  await checkServer();
  setInterval(checkServer, 10000);

  // Poll period status every 30 seconds
  await pollPeriods();
  setInterval(pollPeriods, 30000);
})();
