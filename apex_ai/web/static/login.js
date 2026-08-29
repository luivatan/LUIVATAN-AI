"use strict";

(function initTheme() {
  const stored = localStorage.getItem("apex.theme") || "system";
  const resolved = stored === "system" ? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : stored;
  document.documentElement.dataset.theme = resolved;
})();

const $ = selector => document.querySelector(selector);

let mode = "login"; // "login" | "signup"

function applyMode() {
  const isSignup = mode === "signup";
  $("#authHeading").textContent = isSignup ? "Create your Apex AI account" : "Sign in to Apex AI";
  $("#authSubmitLabel").textContent = isSignup ? "Create account" : "Sign in";
  $("#authDisplayNameField").hidden = !isSignup;
  $("#authPassword").autocomplete = isSignup ? "new-password" : "current-password";
  $("#authToggleMode").innerHTML = isSignup
    ? 'Already have an account? <b>Sign in</b>'
    : 'Need an account? <b>Create one</b>';
  $("#authError").hidden = true;
}

function showError(message) {
  const el = $("#authError");
  el.textContent = message;
  el.hidden = false;
}

async function submitForm(event) {
  event.preventDefault();
  const email = $("#authEmail").value.trim();
  const password = $("#authPassword").value;
  const displayName = $("#authDisplayName").value.trim();
  const submit = $("#authSubmit");
  submit.setAttribute("aria-busy", "true");
  submit.disabled = true;
  try {
    const path = mode === "signup" ? "/auth/signup" : "/auth/login";
    const body = mode === "signup" ? { email, password, display_name: displayName } : { email, password };
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      let message = "That didn't work. Check your details and try again.";
      try {
        const problem = await response.json();
        if (problem && problem.error && problem.error.message) message = problem.error.message;
      } catch (_) { /* keep default message */ }
      showError(message);
      return;
    }
    window.location.href = "/";
  } catch (_) {
    showError("Apex AI could not be reached. Check the connection and try again.");
  } finally {
    submit.removeAttribute("aria-busy");
    submit.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  applyMode();
  $("#authForm").addEventListener("submit", submitForm);
  $("#authToggleMode").addEventListener("click", () => {
    mode = mode === "login" ? "signup" : "login";
    applyMode();
  });
});
