import React, { useState } from "react";

// Common personal/consumer email domains — work emails should not be from these.
const PERSONAL_EMAIL_DOMAINS = new Set([
  "gmail.com","googlemail.com","yahoo.com","yahoo.co.uk","yahoo.co.in","yahoo.fr","yahoo.de",
  "yahoo.es","yahoo.it","yahoo.ca","yahoo.com.au","yahoo.com.br","yahoo.com.mx","yahoo.com.ar",
  "hotmail.com","hotmail.co.uk","hotmail.fr","hotmail.de","hotmail.it","hotmail.es","hotmail.ca",
  "hotmail.com.br","hotmail.com.ar","live.com","live.co.uk","live.fr","live.de","live.it",
  "live.es","live.ca","live.com.au","live.com.br","live.com.ar","outlook.com","outlook.fr",
  "outlook.de","outlook.it","outlook.es","outlook.co.uk","msn.com","passport.com",
  "icloud.com","me.com","mac.com","aol.com","aim.com","verizon.net","att.net","sbcglobal.net",
  "bellsouth.net","comcast.net","cox.net","charter.net","earthlink.net","juno.com",
  "protonmail.com","protonmail.ch","pm.me","tutanota.com","tutanota.de","tutamail.com",
  "tuta.io","keemail.me","zoho.com","yandex.com","yandex.ru","mail.ru","inbox.ru","list.ru",
  "bk.ru","gmx.com","gmx.de","gmx.net","gmx.at","gmx.ch","web.de","freenet.de","t-online.de",
  "mail.com","email.com","usa.com","myself.com","consultant.com","post.com","contractor.net",
  "dr.com","engineer.com","worker.com","techie.com","who.net",
  "rediffmail.com","indiatimes.com","sify.com","in.com","fastmail.com","fastmail.fm",
  "hushmail.com","hush.com","hushmail.me","guerrillamail.com","mailinator.com","throwam.com",
  "sharklasers.com","guerrillamailblock.com","grr.la","guerrillamail.info","guerrillamail.biz",
  "guerrillamail.de","guerrillamail.net","guerrillamail.org","spam4.me","trashmail.com",
  "trashmail.me","trashmail.net","trashmail.org","trashmail.io","dispostable.com",
  "yopmail.com","yopmail.fr","cool.fr.nf","jetable.fr.nf","nospam.ze.tc","nomail.xl.cx",
  "mega.zik.dj","speed.1s.fr","courriel.fr.nf","moncourrier.fr.nf","monemail.fr.nf",
  "monmail.fr.nf","tempmail.com","temp-mail.org","tmpmail.net","tmpmail.org",
  "throwabletransmail.org","throwam.com","rtrtr.com","discard.email",
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
      errors.push({ email, reason: "You cannot share a report with yourself" });
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
          site_id: report.site_id,
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
              {sending ? "Sending..." : "Share report"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
