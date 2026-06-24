import React, { useState } from "react";

const PERSONAL_EMAIL_DOMAINS = new Set([
  "gmail.com","googlemail.com","yahoo.com","hotmail.com","outlook.com","icloud.com","aol.com",
  "protonmail.com","mail.com","live.com","msn.com",
]);

function normalizeEmail(raw) {
  return String(raw || "").trim().toLowerCase();
}

function workEmailError(email) {
  const parts = email.split("@");
  if (!parts[0] || !parts[1] || !parts[1].includes(".")) return "Enter a valid work email.";
  if (PERSONAL_EMAIL_DOMAINS.has(parts[1])) return "Personal email addresses are not accepted.";
  return "";
}

function validateShareEmails(raw, senderEmail) {
  const values = String(raw || "")
    .split(/[,;\s]+/)
    .map(normalizeEmail)
    .filter(Boolean);
  const seen = new Set();
  const valid = [];
  const errors = [];

  values.forEach((email) => {
    if (seen.has(email)) {
      errors.push({ email, reason: "Duplicate address" });
      return;
    }
    seen.add(email);
    if (email === normalizeEmail(senderEmail)) {
      errors.push({ email, reason: "You cannot share a conversation with yourself" });
      return;
    }
    const error = workEmailError(email);
    if (error) errors.push({ email, reason: error });
    else valid.push(email);
  });

  return { valid, errors };
}

function formatApiError(data, fallback = "Request failed") {
  const detail = data?.detail;
  if (typeof detail === "string") return detail;
  if (typeof detail === "object" && detail?.message) return detail.message;
  return data?.message || fallback;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(formatApiError(data));
    error.code = response.status;
    error.payload = data;
    throw error;
  }
  return data;
}

export default function ShareChatDialog({
  siteId,
  sessionId,
  chatTitle,
  companyName,
  senderEmail,
  onClose,
}) {
  const [rawEmails, setRawEmails] = useState("");
  const [errors, setErrors] = useState([]);
  const [results, setResults] = useState([]);
  const [sending, setSending] = useState(false);

  async function submit(event) {
    event.preventDefault();
    const validation = validateShareEmails(rawEmails, senderEmail);
    setErrors(validation.errors);
    setResults([]);
    if (validation.errors.length) return;

    setSending(true);
    try {
      const payload = await fetchJson("/api/chat/share", {
        method: "POST",
        body: JSON.stringify({
          site_id: siteId,
          session_id: sessionId,
          emails: validation.valid,
        }),
      });
      setResults(payload.results || []);
    } catch (error) {
      const recipientErrors = error.payload?.detail?.recipient_errors;
      setErrors(
        Array.isArray(recipientErrors)
          ? recipientErrors
          : [{ email: "", reason: error.message }]
      );
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="review-modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="review-modal share-report-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="shareChatTitle"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="review-modal-head">
          <h2 id="shareChatTitle" className="workspace-card-title">
            Share this chat
          </h2>
          <p className="workspace-page-copy">
            Recipients will also get access to the site report.
          </p>
          <p className="workspace-page-copy">
            {chatTitle ? ` Conversation: ${chatTitle}.` : ""}
            {companyName ? ` Site: ${companyName}.` : ""}
          </p>
        </div>
        <form onSubmit={submit}>
          <label className="workspace-field">
            <span>Recipient work emails</span>
            <textarea
              rows={7}
              value={rawEmails}
              onChange={(event) => {
                setRawEmails(event.target.value);
                setErrors([]);
                setResults([]);
              }}
              placeholder={"person@company.com\nanother@company.com"}
              autoFocus
            />
          </label>
          {errors.length ? (
            <div className="share-recipient-results share-recipient-errors">
              {errors.map((item, index) => (
                <p key={`${item.email}-${index}`}>
                  <strong>{item.email || "Recipients"}:</strong> {item.reason}
                </p>
              ))}
            </div>
          ) : null}
          {results.length ? (
            <div className="share-recipient-results">
              {results.map((item) => (
                <p
                  key={item.email}
                  className={item.status === "sent" ? "share-result-sent" : "share-result-failed"}
                >
                  <strong>{item.email}:</strong>{" "}
                  {item.status === "sent" ? "Email sent" : item.message || "Delivery failed"}
                </p>
              ))}
            </div>
          ) : null}
          <div className="review-modal-actions">
            <button type="button" className="btn-secondary" onClick={onClose} disabled={sending}>
              Close
            </button>
            <button type="submit" className="btn-primary" disabled={sending}>
              {sending ? "Sending..." : "Share conversation"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
