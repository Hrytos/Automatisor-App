import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { loadStripe } from "@stripe/stripe-js";
import { Elements, CardElement, useStripe, useElements } from "@stripe/react-stripe-js";

// ── Brand display map ────────────────────────────────────────
const BRAND_LABELS = {
  visa:       "Visa",
  mastercard: "Mastercard",
  amex:       "Amex",
  discover:   "Discover",
  jcb:        "JCB",
  diners:     "Diners",
  unionpay:   "UnionPay",
};

// ── Saved payment method display ─────────────────────────────
function SavedPaymentMethod({ paymentMethod, onUpdate }) {
  const brand = (paymentMethod.brand || "").toLowerCase();
  const label = BRAND_LABELS[brand] || paymentMethod.brand || "Card";
  const expMonth = String(paymentMethod.exp_month).padStart(2, "0");
  const expYear = String(paymentMethod.exp_year).slice(-2);

  return (
    <div className="pm-saved-card">
      <div className="pm-card-left">
        <span className={`pm-card-brand-pill pm-brand-${brand}`}>{label}</span>
        <div className="pm-card-meta">
          <span className="pm-card-number">•••• •••• •••• {paymentMethod.last4}</span>
          <span className="pm-card-expiry">Expires {expMonth}/{expYear}</span>
        </div>
      </div>
      <button className="btn-secondary btn-sm" onClick={onUpdate}>
        Update card
      </button>
    </div>
  );
}

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

// ── Card setup form (rendered inside Stripe Elements) ────────
function CardSetupForm({ email, customerId, onSuccess, onCancel }) {
  const stripe = useStripe();
  const elements = useElements();
  const [busy, setBusy] = useState(false);
  const [cardError, setCardError] = useState("");
  const [cardStatus, setCardStatus] = useState("");

  async function handleSubmit(e) {
    e?.preventDefault();
    if (!stripe || !elements) {
      setCardError("Payment form is still loading. Please wait a moment and try again.");
      return;
    }
    setBusy(true);
    setCardError("");
    setCardStatus("Preparing secure card form…");
    try {
      const { client_secret } = await fetchJson("/api/stripe/setup-intent", {
        method: "POST",
        body: JSON.stringify({ email, customer_id: customerId }),
      });
      setCardStatus("Confirming card with Stripe…");
      const { setupIntent, error } = await stripe.confirmCardSetup(client_secret, {
        payment_method: { card: elements.getElement(CardElement) },
      });
      if (error) {
        setCardError(error.message || "Card setup failed.");
        setCardStatus("");
        return;
      }
      setCardStatus("Saving card to your account…");
      await fetchJson("/api/stripe/confirm-payment-method", {
        method: "POST",
        body: JSON.stringify({
          email,
          customer_id: customerId,
          payment_method_id: setupIntent.payment_method,
        }),
      });
      setCardStatus("Card saved. Refreshing billing details…");
      onSuccess();
    } catch (err) {
      setCardError(err.message || "Something went wrong.");
      setCardStatus("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="card-setup-form">
      <div className="card-element-wrap">
        <CardElement
          options={{
            style: {
              base: { fontSize: "15px", color: "#1a1a1a", "::placeholder": { color: "#999" } },
              invalid: { color: "#e53935" },
            },
          }}
        />
      </div>
      {cardError && <p className="form-error" style={{ marginTop: "8px" }}>{cardError}</p>}
      {cardStatus && <p className="card-setup-hint">{cardStatus}</p>}
      <div className="card-setup-actions">
        <button type="button" className="btn-primary" onClick={handleSubmit} disabled={busy || !stripe}>
          {busy ? "Saving…" : "Save card"}
        </button>
        <button type="button" className="btn-secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </form>
  );
}

// ── Page component ───────────────────────────────────────────
export default function BillingPage() {
  const [session] = useRequireSession();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [stripeInitError, setStripeInitError] = useState("");
  const [stripePromise, setStripePromise] = useState(null);
  const [invoices, setInvoices] = useState([]);
  const [paymentMethod, setPaymentMethod] = useState(null);
  const [showCardSetup, setShowCardSetup] = useState(false);
  const [payingInvoiceId, setPayingInvoiceId] = useState(null);
  const [payError, setPayError] = useState("");

  function loadData(silent = false) {
    if (!session?.email) return;
    if (!silent) setLoading(true);
    fetchJson("/api/billing/invoices", {
      method: "POST",
      body: JSON.stringify({ email: session.email }),
    })
      .then((payload) => {
        setInvoices(payload.invoices || []);
        setPaymentMethod(payload.payment_method || null);
      })
      .catch((err) => setError(err.message || "Could not load invoices."))
      .finally(() => setLoading(false));
  }

  useEffect(() => { loadData(); }, [session?.email]);

  useEffect(() => {
    let active = true;
    fetchJson("/api/frontend-config")
      .then((payload) => {
        const key = String(payload.stripe_publishable_key || "").trim();
        if (!key) {
          throw new Error("Stripe publishable key is missing in server configuration.");
        }
        const promise = loadStripe(key);
        promise
          .then((instance) => {
            if (active && !instance) {
              setStripeInitError("Stripe.js failed to initialize. Disable blockers and refresh the page.");
            }
          })
          .catch((err) => {
            if (active) {
              setStripeInitError(err.message || "Unable to initialize payment form.");
            }
          });
        if (active) {
          setStripePromise(promise);
        }
      })
      .catch((err) => {
        if (active) {
          setStripeInitError(err.message || "Unable to initialize payment form.");
        }
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    function onFocus() { loadData(true); }
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [session?.email]);

  async function handlePayInvoice(invoiceId) {
    const inv = invoices.find((i) => i.invoice_id === invoiceId);

    // Open invoices already have a hosted_invoice_url — go straight there
    if (inv?.payment_url) {
      window.open(inv.payment_url, "_blank", "noopener,noreferrer");
      return;
    }

    // Draft invoices need to be finalized on the backend first to get a URL
    setPayingInvoiceId(invoiceId);
    setPayError("");
    try {
      const { url } = await fetchJson("/api/billing/get-invoice-url", {
        method: "POST",
        body: JSON.stringify({ email: session.email, invoice_id: invoiceId }),
      });
      window.open(url, "_blank", "noopener,noreferrer");
      // Reload so the draft now shows as open with its hosted URL
      loadData();
    } catch (err) {
      setPayError(err.message || "Could not open payment page. Please try again.");
    } finally {
      setPayingInvoiceId(null);
    }
  }

  if (!session?.email) return null;

  const openInvoiceCount = invoices.filter((inv) => inv.status === "open" || inv.status === "draft").length;

  return (
    <main className="workspace-page-shell signup-body workspace-body">
      <section className="workspace-page account-page">
        <header className="workspace-topbar workspace-topbar-titleonly">
          <div className="workspace-topbar-copy">
            <p className="workspace-eyebrow">Account</p>
            <h1 className="workspace-page-title">Payments &amp; Invoices</h1>
          </div>
        </header>

        {/* ── Payment method section ─────────────────────── */}
        <section className="workspace-card workspace-card-modern payment-method-section">
          <div className="payment-method-header">
            <div>
              <h2 className="workspace-section-title">Payment Method</h2>
              <p className="workspace-section-subtitle">
                {paymentMethod
                  ? "Your card will be charged automatically at the end of each billing period."
                  : "Add a card to enable automatic billing at the end of each period."}
              </p>
            </div>
            {!loading && !showCardSetup && !paymentMethod && (
              <button
                className="btn-primary"
                onClick={() => setShowCardSetup(true)}
              >
                Add payment method
              </button>
            )}
          </div>

          {loading ? (
            <div className="pm-loading">Loading payment details…</div>
          ) : paymentMethod && !showCardSetup ? (
            <SavedPaymentMethod
              paymentMethod={paymentMethod}
              onUpdate={() => setShowCardSetup(true)}
            />
          ) : showCardSetup ? (
            stripeInitError ? (
              <p className="form-error" style={{ marginTop: "8px" }}>{stripeInitError}</p>
            ) : stripePromise ? (
              <Elements stripe={stripePromise}>
                <CardSetupForm
                  email={session.email}
                  customerId={session.customerId}
                  onSuccess={() => { setShowCardSetup(false); loadData(); }}
                  onCancel={() => setShowCardSetup(false)}
                />
              </Elements>
            ) : (
              <div className="pm-loading">Loading payment form…</div>
            )
          ) : null}
        </section>

        <p className={`form-error ${error ? "" : "hidden"}`}>{error}</p>

        {/* ── Invoices section ───────────────────────────── */}
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
            <div className="invoices-section-header">
              <h2 className="workspace-section-title">
                Invoices
                {openInvoiceCount > 0 && (
                  <span className="invoices-open-badge">{openInvoiceCount} due</span>
                )}
              </h2>
            </div>
            {payError && (
              <p className="form-error" style={{ marginBottom: "12px" }}>{payError}</p>
            )}
            <div className="invoices-table-wrap">
              <table className="invoices-table">
                <thead>
                  <tr>
                    <th>Invoice #</th>
                    <th>Date</th>
                    <th>Billing period</th>
                    <th className="invoices-col-right">Amount</th>
                    <th>Status</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {invoices.map((inv) => (
                    <tr
                      key={inv.invoice_id}
                      className={(inv.status === "open" || inv.status === "draft") ? "invoice-row-open" : ""}
                    >
                      <td className="invoices-number">{inv.invoice_number || "—"}</td>
                      <td className="invoices-date">{formatInvoiceDate(inv.invoice_date)}</td>
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
                        {(inv.status === "open" || inv.status === "draft") ? (
                          <button
                            className="invoice-pay-btn"
                            onClick={() => handlePayInvoice(inv.invoice_id)}
                            disabled={payingInvoiceId === inv.invoice_id}
                          >
                            {payingInvoiceId === inv.invoice_id ? "Opening…" : "Pay now"}
                          </button>
                        ) : inv.payment_url ? (
                          <a
                            href={inv.payment_url}
                            className="invoices-action-link"
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            Receipt ↗
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
