"use strict";

const conversation = document.querySelector("#conversation");
const welcome = document.querySelector("#welcome");
const form = document.querySelector("#chat-form");
const input = document.querySelector("#message-input");
const sendButton = document.querySelector("#send-button");
const loadingLabel = document.querySelector("#loading-label");
const errorBanner = document.querySelector("#error-banner");
const systemState = document.querySelector("#system-state");
const statusLabel = document.querySelector("#status-label");
const promptButtons = document.querySelectorAll("[data-prompt]");

const messages = [];
let isLoading = false;

function setLoading(loading) {
  isLoading = loading;
  input.disabled = loading;
  sendButton.disabled = loading;
  sendButton.classList.toggle("is-loading", loading);
  loadingLabel.textContent = loading
    ? "Researching through MCP tools..."
    : "";
}

function showError(message) {
  errorBanner.textContent = message;
  errorBanner.hidden = false;
}

function clearError() {
  errorBanner.textContent = "";
  errorBanner.hidden = true;
}

function appendMessage(role, content, trace = []) {
  if (welcome) {
    welcome.hidden = true;
  }

  const article = document.createElement("article");
  article.className = `message message-${role}`;

  const label = document.createElement("p");
  label.className = "message-label";
  label.textContent = role === "user" ? "You" : "Market Thread";

  const body = document.createElement("div");
  body.className = "message-body";
  body.textContent = content;

  article.append(label, body);
  if (role === "assistant" && trace.length > 0) {
    article.append(createTrace(trace));
  }
  conversation.append(article);
  conversation.scrollTop = conversation.scrollHeight;
}

function createTrace(trace) {
  const details = document.createElement("details");
  details.className = "tool-trace";

  const summary = document.createElement("summary");
  summary.textContent = `${trace.length} MCP tool ${trace.length === 1 ? "step" : "steps"}`;
  details.append(summary);

  const list = document.createElement("ol");
  trace.forEach((entry) => {
    const item = document.createElement("li");
    item.className = `trace-${entry.status}`;

    const heading = document.createElement("strong");
    heading.textContent = entry.tool;

    const argumentsLine = document.createElement("code");
    argumentsLine.textContent = JSON.stringify(entry.arguments);

    const result = document.createElement("span");
    result.textContent = entry.summary;

    item.append(heading, argumentsLine, result);
    list.append(item);
  });
  details.append(list);
  return details;
}

async function sendMessage(text) {
  if (isLoading || !text.trim()) {
    return;
  }

  clearError();
  const userMessage = text.trim();
  messages.push({ role: "user", content: userMessage });
  appendMessage("user", userMessage);
  input.value = "";
  setLoading(true);

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages }),
    });
    const payload = await response.json();
    if (!response.ok) {
      const detail = payload.detail;
      throw new Error(
        detail && typeof detail.message === "string"
          ? detail.message
          : "The research request failed."
      );
    }

    messages.push({ role: "assistant", content: payload.answer });
    appendMessage("assistant", payload.answer, payload.trace || []);
  } catch (error) {
    showError(
      error instanceof Error
        ? error.message
        : "The research request could not be completed."
    );
  } finally {
    setLoading(false);
    input.focus();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  void sendMessage(input.value);
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

promptButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const prompt = button.dataset.prompt || "";
    input.value = prompt;
    input.focus();
  });
});

async function checkHealth() {
  try {
    const response = await fetch("/api/health");
    if (!response.ok) {
      throw new Error("Health check failed");
    }
    const health = await response.json();
    const ready = health.status === "ready";
    systemState.classList.toggle("is-ready", ready);
    systemState.classList.toggle("needs-config", !ready);
    statusLabel.textContent = ready
      ? `${health.model || "Model"} ready`
      : "Configuration required";
  } catch {
    systemState.classList.add("needs-config");
    statusLabel.textContent = "Backend unavailable";
  }
}

void checkHealth();
