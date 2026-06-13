import React, { useState } from "react";
import { DOMAINS as PERSONAL_EMAIL_DOMAINS } from "free-email-domains-list";

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
      errors.push({ email, reason: "You cannot share a report with yourself" });
      return;
    }
    const error = workEmailError(email);
    if (error) errors.push({ email, reason: error });
    else valid.push(email);
  });

  return { valid, errors };
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.message || data.detail || "Request failed");
    error.code = response.status;
    error.payload = data;
    throw error;
  }
  return data;
}

export default function ShareReportDialog({ report, senderEmail, onClose }) {
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
      const payload = await fetchJson("/api/reports/share", {
        method: "POST",
        body: JSON.stringify({
          customer_site_id: report.customer_site_id,
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
        aria-labelledby="shareReportTitle"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="review-modal-head">
          <h2 id="shareReportTitle" className="workspace-card-title">
            Share Report
          </h2>
          <p className="workspace-page-copy">
            You can only share to work emails. They will verify an OTP before the report can be accessed by them.
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
                  className={
                    item.status === "sent"
                      ? "share-result-sent"
                      : item.status === "already_shared"
                      ? "share-result-warning"
                      : "share-result-failed"
                  }
                >
                  <strong>{item.email}:</strong>{" "}
                  {item.status === "sent"
                    ? "Email sent"
                    : item.status === "already_shared"
                    ? "Already shared this report with this user"
                    : item.message || "Delivery failed"}
                </p>
              ))}
            </div>
          ) : null}
          <div className="review-modal-actions">
            <button type="button" className="btn-secondary" onClick={onClose} disabled={sending}>
              Close
            </button>
            <button type="submit" className="btn-primary" disabled={sending}>
              {sending ? "Sending..." : "Share report"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
