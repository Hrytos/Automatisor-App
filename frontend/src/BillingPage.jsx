import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

// ── Shared session utilities ─────────────────────────────────
const SESSION_KEY = "automatisor_auth_workspace_v2";

function loadSession() {
  try {
    const raw = window.sessionStorage.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

async function fetchJson(url, options = {}) {
  const res = await fetch(url, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    const error = new Error(payload.detail || "Request failed.");
    error.code = payload.code || res.headers.get("x-error-code") || "";
    error.payload = payload;
    throw error;
  }
  return payload;
}

function useRequireSession() {
  const navigate = useNavigate();
  const [session, setSession] = useState(() => loadSession());
  useEffect(() => {
    if (!session || !session.email) {
      navigate("/auth", { replace: true });
    }
  }, [navigate, session]);
  return [session, setSession];
}

// ── Date + currency formatting ───────────────────────────────
function formatInvoiceDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "America/New_York",
  }).format(date);
}

function formatCurrency(amount) {
  if (amount == null || amount === "") return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(Number(amount));
}

// ── Page component ───────────────────────────────────────────
export default function BillingPage() {
  const [session] = useRequireSession();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [invoices, setInvoices] = useState([]);

  useEffect(() => {
    if (!session?.email) return;
    setLoading(true);
    fetchJson("/api/billing/invoices", {
      method: "POST",
      body: JSON.stringify({ email: session.email }),
    })
      .then((payload) => setInvoices(payload.invoices || []))
      .catch((err) => setError(err.message || "Could not load invoices."))
      .finally(() => setLoading(false));
  }, [session?.email]);

  if (!session?.email) return null;

  return (
    <main className="workspace-page-shell signup-body workspace-body">
      <section className="workspace-page account-page">
        <header className="workspace-topbar">
          <div className="workspace-topbar-copy">
            <p className="workspace-eyebrow">Account</p>
            <h1 className="workspace-page-title">Payments &amp; Invoices</h1>
          </div>
          <div className="workspace-topbar-actions">
            <Link to="/workspace/credits" className="btn-secondary">
              Credits &amp; Usage
            </Link>
            <Link to="/workspace" className="btn-secondary">
              Back to workspace
            </Link>
          </div>
        </header>

        <p className={`form-error ${error ? "" : "hidden"}`}>{error}</p>

        {loading ? (
          <div className="workspace-loading-state">
            <p>Loading invoices…</p>
          </div>
        ) : invoices.length === 0 ? (
          <div className="workspace-card workspace-card-modern">
            <div className="workspace-empty-state">
              <h3>No invoices yet</h3>
              <p>Your invoices will appear here once they have been issued.</p>
            </div>
          </div>
        ) : (
          <section className="invoices-section">
            <div className="invoices-table-wrap">
              <table className="invoices-table">
                <thead>
                  <tr>
                    <th>Invoice #</th>
                    <th>Date</th>
                    <th>Billing period</th>
                    <th className="invoices-col-right">Amount</th>
                    <th>Status</th>
                    <th>Invoice</th>
                    <th>Receipt</th>
                  </tr>
                </thead>
                <tbody>
                  {invoices.map((inv) => (
                    <tr key={inv.invoice_id}>
                      <td className="invoices-number">{inv.invoice_number || "—"}</td>
                      <td>{formatInvoiceDate(inv.invoice_date)}</td>
                      <td className="invoices-period">
                        {inv.period_start && inv.period_end
                          ? `${formatInvoiceDate(inv.period_start)} – ${formatInvoiceDate(inv.period_end)}`
                          : "—"}
                      </td>
                      <td className="invoices-col-right invoices-amount">
                        {formatCurrency(inv.amount_usd)}
                      </td>
                      <td>
                        <span
                          className={`invoice-status-badge invoice-status-${inv.status || "pending"}`}
                        >
                          {inv.status
                            ? inv.status.charAt(0).toUpperCase() + inv.status.slice(1)
                            : "Pending"}
                        </span>
                      </td>
                      <td>
                        {inv.pdf_url ? (
                          <a
                            href={inv.pdf_url}
                            className="invoices-action-link"
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            Download
                          </a>
                        ) : (
                          <span className="invoices-na">—</span>
                        )}
                      </td>
                      <td>
                        {inv.payment_url ? (
                          <a
                            href={inv.payment_url}
                            className="invoices-action-link"
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            View
                          </a>
                        ) : (
                          <span className="invoices-na">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </section>
    </main>
  );
}
