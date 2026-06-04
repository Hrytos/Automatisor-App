import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

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

// ── Date formatting ──────────────────────────────────────────
function formatDateTimeEST(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "America/New_York",
  }).format(date);
}

function formatPeriodDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "America/New_York",
  }).format(date);
}

// ── Page component ───────────────────────────────────────────
export default function CreditsPage() {
  const [session] = useRequireSession();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [data, setData] = useState(null);
  const [expandedPeriods, setExpandedPeriods] = useState(new Set());
  const [siteFilter, setSiteFilter] = useState("");
  const [taskFilter, setTaskFilter] = useState("");
  const [sortOrder, setSortOrder] = useState("newest");

  useEffect(() => {
    if (!session?.email) return;
    setLoading(true);
    fetchJson("/api/credits/usage", {
      method: "POST",
      body: JSON.stringify({ email: session.email }),
    })
      .then((payload) => {
        setData(payload);
        const current = (payload.billing_periods || []).find((p) => p.is_current);
        if (current !== undefined) {
          setExpandedPeriods(new Set([current.period_index]));
        }
      })
      .catch((err) => setError(err.message || "Could not load usage history."))
      .finally(() => setLoading(false));
  }, [session?.email]);

  function togglePeriod(index) {
    setExpandedPeriods((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

  const allSiteOptions = useMemo(() => {
    if (!data) return [];
    const seen = new Map();
    (data.billing_periods || []).forEach((p) =>
      p.rows.forEach((r) => {
        if (r.site_address && r.site_address !== "\u2014" && !seen.has(r.site_address)) {
          seen.set(r.site_address, r.site_name || r.site_address);
        }
      })
    );
    return Array.from(seen.entries())
      .map(([addr, name]) => ({ addr, name }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [data]);

  const allTaskLabels = useMemo(() => {
    if (!data) return [];
    const labels = new Set();
    (data.billing_periods || []).forEach((p) =>
      p.rows.forEach((r) => {
        if (r.task_label) labels.add(r.task_label);
      })
    );
    return Array.from(labels).sort();
  }, [data]);

  function filterRows(rows) {
    let result = [...rows];
    if (siteFilter) {
      result = result.filter((r) => r.site_address === siteFilter);
    }
    if (taskFilter) {
      result = result.filter((r) => r.task_label === taskFilter);
    }
    result.sort((a, b) =>
      sortOrder === "oldest"
        ? new Date(a.timestamp_utc) - new Date(b.timestamp_utc)
        : new Date(b.timestamp_utc) - new Date(a.timestamp_utc)
    );
    return result;
  }

  if (!session?.email) return null;

  const periods = data?.billing_periods || [];
  const currentPeriod = periods.find((p) => p.is_current);

  return (
    <main className="workspace-page-shell signup-body workspace-body">
      <section className="workspace-page account-page">
        <header className="workspace-topbar workspace-topbar-titleonly">
          <div className="workspace-topbar-copy">
            <p className="workspace-eyebrow">Account</p>
            <h1 className="workspace-page-title">Credits &amp; Usage</h1>
          </div>
        </header>

        <p className={`form-error ${error ? "" : "hidden"}`}>{error}</p>

        {loading ? (
          <div className="workspace-loading-state">
            <p>Loading usage history…</p>
          </div>
        ) : (
          <>
            {/* Current billing period summary card */}
            {currentPeriod ? (
              <section className="credits-period-summary">
                <div className="credits-summary-card">
                  <p className="workspace-eyebrow">Current billing period</p>
                  <div className="credits-summary-row">
                    <div className="credits-summary-stat">
                      <span className="credits-summary-value">{currentPeriod.total_credits_used}</span>
                      <span className="credits-summary-label">Credits used</span>
                    </div>
                    <div className="credits-summary-divider" />
                    <div className="credits-summary-dates">
                      <p className="credits-period-note">30-day rolling period</p>
                    </div>
                  </div>
                </div>
              </section>
            ) : null}

            {/* Usage history */}
            <section className="credits-history-section">
              <h2 className="credits-history-heading">Usage history</h2>
              {periods.length === 0 ? (
                <div className="workspace-empty-state">
                  <p>No usage recorded yet.</p>
                </div>
              ) : (
                <>
                  <div className="credits-filter-bar">
                    <select
                      className="credits-filter-select"
                      value={siteFilter}
                      onChange={(e) => setSiteFilter(e.target.value)}
                    >
                      <option value="">All facilities</option>
                      {allSiteOptions.map(({ addr, name }) => (
                        <option key={addr} value={addr}>{name} — {addr}</option>
                      ))}
                    </select>
                    <select
                      className="credits-filter-select"
                      value={taskFilter}
                      onChange={(e) => setTaskFilter(e.target.value)}
                    >
                      <option value="">All tasks</option>
                      {allTaskLabels.map((label) => (
                        <option key={label} value={label}>{label}</option>
                      ))}
                    </select>
                    <select
                      className="credits-filter-select"
                      value={sortOrder}
                      onChange={(e) => setSortOrder(e.target.value)}
                    >
                      <option value="newest">Newest first</option>
                      <option value="oldest">Oldest first</option>
                    </select>
                    {(siteFilter || taskFilter || sortOrder !== "newest") && (
                      <button
                        type="button"
                        className="credits-filter-clear"
                        onClick={() => { setSiteFilter(""); setTaskFilter(""); setSortOrder("newest"); }}
                      >
                        Clear filters
                      </button>
                    )}
                  </div>
                  <div className="credits-period-list">
                    {periods.map((period) => {
                      const filteredRows = filterRows(period.rows);
                      const hasActiveFilter = !!(siteFilter || taskFilter);
                      if (hasActiveFilter && filteredRows.length === 0) return null;
                      const isExpanded = expandedPeriods.has(period.period_index) || hasActiveFilter;
                      const isFreePeriod = period.rows.length > 0 && period.rows.every((r) => r.is_free);
                      return (
                        <div
                          key={period.period_index}
                          className={`credits-period-block${period.is_current ? " credits-period-block--current" : ""}`}
                        >
                          <button
                            type="button"
                            className="credits-period-toggle"
                            onClick={() => togglePeriod(period.period_index)}
                            aria-expanded={isExpanded}
                          >
                            <div className="credits-period-toggle-left">
                              <span className="credits-period-range-text">
                                {formatPeriodDate(period.period_start)} – {formatPeriodDate(period.period_end)}
                              </span>
                              {isFreePeriod && (
                                <span className="credits-free-badge">Free period</span>
                              )}
                            </div>
                            <div className="credits-period-toggle-right">
                              <span className="credits-period-credits">
                                {period.total_credits_used}{" "}
                                {period.total_credits_used === 1 ? "credit" : "credits"}
                              </span>
                              <svg
                                className={`credits-chevron${isExpanded ? " credits-chevron--open" : ""}`}
                                aria-hidden="true"
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="2"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                              >
                                <path d="m6 9 6 6 6-6" />
                              </svg>
                            </div>
                          </button>

                          {isExpanded ? (
                            <div className="credits-period-rows">
                              {filteredRows.length === 0 ? (
                                <p className="credits-empty-period">No usage in this period.</p>
                              ) : (
                                <table className="credits-history-table">
                                  <thead>
                                    <tr>
                                      <th>Site</th>
                                      <th>Task</th>
                                      <th>Date (EST)</th>
                                      <th className="credits-col-right">Credits</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {filteredRows.map((row, i) => (
                                      <tr key={i}>
                                        <td>
                                          <span className="credits-site-name">{row.site_name}</span>
                                          {row.site_address && row.site_address !== "—" ? (
                                            <span className="credits-site-address">{row.site_address}</span>
                                          ) : null}
                                        </td>
                                        <td>{row.task_label}</td>
                                        <td className="credits-timestamp">{formatDateTimeEST(row.timestamp_utc)}</td>
                                        <td className="credits-col-right">
                                          {row.credits_used}
                                          {row.is_free && (
                                            <span className="credits-free-row-tag" title="Not billed — free period"> free</span>
                                          )}
                                        </td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              )}
                            </div>
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                </>
              )}
            </section>
          </>
        )}
      </section>
    </main>
  );
}
