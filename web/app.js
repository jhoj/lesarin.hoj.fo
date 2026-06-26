"use strict";

// Tiny dependency-free client for the Lesarin SaaS surface. State is just the
// bearer token (in localStorage) plus the canonical-field list fetched once.

const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "lesarin.token";

let token = localStorage.getItem(TOKEN_KEY) || null;
let canonicalFields = [];
let profiles = [];
let editingProfileId = null;
let pickedFile = null;
let lastExport = null; // { blob, filename }

// --- API helper ------------------------------------------------------------

async function api(path, { method = "GET", body, isForm = false } = {}) {
  const headers = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (body && !isForm) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method,
    headers,
    body: isForm ? body : body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    setToken(null);
    throw new Error("Session expired — please log in again.");
  }
  return res;
}

async function apiJson(path, opts) {
  const res = await api(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}

// --- Auth ------------------------------------------------------------------

let authMode = "login";

function renderAuthMode() {
  const isLogin = authMode === "login";
  $("auth-title").textContent = isLogin ? "Log in" : "Create your account";
  $("auth-submit").textContent = isLogin ? "Log in" : "Sign up";
  $("auth-switch-text").textContent = isLogin ? "No account yet?" : "Already have an account?";
  $("auth-switch").textContent = isLogin ? "Create one" : "Log in";
  $("password").autocomplete = isLogin ? "current-password" : "new-password";
  $("auth-error").textContent = "";
}

$("auth-switch").addEventListener("click", (e) => {
  e.preventDefault();
  authMode = authMode === "login" ? "register" : "login";
  renderAuthMode();
});

$("auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("auth-error").textContent = "";
  try {
    const data = await apiJson(`/api/auth/${authMode}`, {
      method: "POST",
      body: { email: $("email").value, password: $("password").value },
    });
    setToken(data.token);
  } catch (err) {
    $("auth-error").textContent = err.message;
  }
});

$("logout").addEventListener("click", () => setToken(null));

function setToken(value) {
  token = value;
  if (value) localStorage.setItem(TOKEN_KEY, value);
  else localStorage.removeItem(TOKEN_KEY);
  route();
}

// --- Routing between auth screen and app ----------------------------------

async function route() {
  const authed = !!token;
  $("auth").hidden = authed;
  $("app").hidden = !authed;
  $("logout").hidden = !authed;
  if (!authed) {
    renderAuthMode();
    return;
  }
  try {
    if (!canonicalFields.length) {
      canonicalFields = await apiJson("/api/canonical-fields");
    }
    await loadProfiles();
  } catch (err) {
    // Bad/expired token: fall back to the auth screen.
    setToken(null);
  }
}

// --- Profiles --------------------------------------------------------------

async function loadProfiles() {
  profiles = await apiJson("/api/me/profiles");
  renderProfileList();
  renderExportProfiles();
}

function renderProfileList() {
  const list = $("profile-list");
  list.innerHTML = "";
  if (!profiles.length) {
    list.innerHTML = `<li class="muted">No profiles yet — create one to choose your output.</li>`;
    return;
  }
  for (const p of profiles) {
    const li = document.createElement("li");
    li.innerHTML = `
      <span class="name"></span>
      <span class="badge fmt"></span>
      ${p.is_default ? '<span class="badge">default</span>' : ""}
      <span class="spacer"></span>
      <button class="ghost edit">Edit</button>`;
    li.querySelector(".name").textContent = p.name;
    li.querySelector(".fmt").textContent = p.fmt;
    li.querySelector(".edit").addEventListener("click", () => openProfile(p));
    list.appendChild(li);
  }
}

function renderExportProfiles() {
  const sel = $("export-profile");
  sel.innerHTML = "";
  for (const p of profiles) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = `${p.name} (${p.fmt})`;
    if (p.is_default) opt.selected = true;
    sel.appendChild(opt);
  }
}

function blankProfile() {
  return {
    id: null,
    name: "",
    fmt: "json",
    is_default: profiles.length === 0,
    fields: canonicalFields.map((f) => ({ canonical: f.key, output_name: f.key, on: true })),
  };
}

$("profile-new").addEventListener("click", () => openProfile(null));
$("profile-cancel").addEventListener("click", () => ($("profile-form").hidden = true));

function openProfile(p) {
  editingProfileId = p ? p.id : null;
  $("profile-form").hidden = false;
  $("profile-form-title").textContent = p ? `Edit “${p.name}”` : "New profile";
  $("profile-name").value = p ? p.name : "";
  $("profile-format").value = p ? p.fmt : "json";
  $("profile-default").checked = p ? p.is_default : profiles.length === 0;
  $("profile-delete").hidden = !p;
  $("profile-error").textContent = "";

  const chosen = new Map((p ? p.fields : []).map((f) => [f.canonical, f.output_name]));
  const tbody = $("profile-fields");
  tbody.innerHTML = "";
  for (const f of canonicalFields) {
    const on = p ? chosen.has(f.key) : true;
    const name = p ? chosen.get(f.key) ?? f.key : f.key;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="checkbox" class="on" ${on ? "checked" : ""} /></td>
      <td class="canon"><span></span><small></small></td>
      <td><input class="oname" value="" /></td>`;
    tr.querySelector(".canon span").textContent = f.key;
    tr.querySelector(".canon small").textContent = f.display_name;
    const oname = tr.querySelector(".oname");
    oname.value = name;
    oname.dataset.canonical = f.key;
    tbody.appendChild(tr);
  }
  $("profile-form").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

$("profile-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("profile-error").textContent = "";
  const fields = [];
  for (const tr of $("profile-fields").querySelectorAll("tr")) {
    if (!tr.querySelector(".on").checked) continue;
    const input = tr.querySelector(".oname");
    fields.push({ canonical: input.dataset.canonical, output_name: input.value.trim() || input.dataset.canonical });
  }
  if (!fields.length) {
    $("profile-error").textContent = "Pick at least one field.";
    return;
  }
  const body = {
    name: $("profile-name").value.trim() || "Untitled",
    fmt: $("profile-format").value,
    is_default: $("profile-default").checked,
    fields,
  };
  try {
    if (editingProfileId) {
      await apiJson(`/api/me/profiles/${editingProfileId}`, { method: "PUT", body });
    } else {
      await apiJson("/api/me/profiles", { method: "POST", body });
    }
    $("profile-form").hidden = true;
    await loadProfiles();
  } catch (err) {
    $("profile-error").textContent = err.message;
  }
});

$("profile-delete").addEventListener("click", async () => {
  if (!editingProfileId) return;
  try {
    await api(`/api/me/profiles/${editingProfileId}`, { method: "DELETE" });
    $("profile-form").hidden = true;
    await loadProfiles();
  } catch (err) {
    $("profile-error").textContent = err.message;
  }
});

// --- Export ----------------------------------------------------------------

const dropzone = $("dropzone");
$("pick").addEventListener("click", () => $("file").click());
$("file").addEventListener("change", () => setFile($("file").files[0]));

["dragenter", "dragover"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.add("drag");
  })
);
["dragleave", "drop"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.remove("drag");
  })
);
dropzone.addEventListener("drop", (e) => {
  const f = e.dataTransfer.files[0];
  if (f) setFile(f);
});

function setFile(f) {
  pickedFile = f || null;
  $("export-run").disabled = !pickedFile;
  dropzone.classList.toggle("has-file", !!pickedFile);
  dropzone.querySelector("span").textContent = pickedFile
    ? `Ready: ${pickedFile.name}`
    : "";
  if (!pickedFile) {
    dropzone.innerHTML = `<input type="file" id="file" accept="application/pdf,.pdf" hidden /><span>Drop a PDF here or <button type="button" id="pick" class="link">choose a file</button></span>`;
  }
}

$("export-run").addEventListener("click", async () => {
  if (!pickedFile) return;
  const profileId = $("export-profile").value;
  const fmt = $("export-format").value;
  const params = new URLSearchParams();
  if (profileId) params.set("profile_id", profileId);
  if (fmt) params.set("fmt", fmt);
  const form = new FormData();
  form.append("file", pickedFile);

  $("export-status").textContent = "Reading…";
  $("export-run").disabled = true;
  try {
    const res = await api(`/api/me/export?${params.toString()}`, { method: "POST", body: form, isForm: true });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Export failed (${res.status})`);
    }
    const blob = await res.blob();
    const text = await blob.text();
    const cd = res.headers.get("Content-Disposition") || "";
    const m = cd.match(/filename="?([^"]+)"?/);
    lastExport = { blob, filename: m ? m[1] : "invoice.txt" };
    $("export-output").textContent = text;
    $("export-output").hidden = false;
    $("export-download").disabled = false;
    $("export-status").textContent = "Done.";
  } catch (err) {
    $("export-status").textContent = err.message;
  } finally {
    $("export-run").disabled = !pickedFile;
  }
});

$("export-download").addEventListener("click", () => {
  if (!lastExport) return;
  const url = URL.createObjectURL(lastExport.blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = lastExport.filename;
  a.click();
  URL.revokeObjectURL(url);
});

// --- Boot ------------------------------------------------------------------

route();
