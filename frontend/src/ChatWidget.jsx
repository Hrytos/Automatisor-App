import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation, useNavigate } from "react-router-dom";
import ShareChatDialog from "./ShareChatDialog.jsx";

/**
 * Render a subset of markdown to React elements:
 *   **bold**, *italic*, `code`, bullets, numbered lists, blank-line paragraphs.
 */
function renderMarkdown(text) {
  if (!text) return null;

  const lines = text.split("\n");
  const blocks = [];
  let paragraphLines = [];
  let listBlock = null;

  function flushParagraph() {
    if (!paragraphLines.length) return;
    blocks.push({ type: "p", lines: [...paragraphLines] });
    paragraphLines = [];
  }

  function flushList() {
    if (!listBlock) return;
    if (listBlock.currentItem.trim()) {
      listBlock.items.push(listBlock.currentItem.trim());
    }
    if (listBlock.items.length) {
      blocks.push({ type: listBlock.type, items: [...listBlock.items] });
    }
    listBlock = null;
  }

  lines.forEach((rawLine) => {
    const line = rawLine.trimEnd();
    const trimmed = line.trim();
    const unorderedMatch = trimmed.match(/^[-*]\s+(.+)$/);
    const orderedMatch = trimmed.match(/^\d+\.\s+(.+)$/);

    if (!trimmed) {
      flushList();
      flushParagraph();
      return;
    }

    if (unorderedMatch || orderedMatch) {
      const type = unorderedMatch ? "ul" : "ol";
      const itemText = (unorderedMatch ? unorderedMatch[1] : orderedMatch[1]).trim();

      flushParagraph();
      if (!listBlock || listBlock.type !== type) {
        flushList();
        listBlock = { type, items: [], currentItem: "" };
      }
      if (listBlock.currentItem.trim()) {
        listBlock.items.push(listBlock.currentItem.trim());
      }
      listBlock.currentItem = itemText;
      return;
    }

    if (listBlock) {
      listBlock.currentItem = `${listBlock.currentItem} ${trimmed}`.trim();
      return;
    }

    paragraphLines.push(line);
  });

  flushList();
  flushParagraph();

  return blocks.map((block, bi) => {
    if (block.type === "ul") {
      return (
        <ul key={bi} className="chat-md-list">
          {block.items.map((item, ii) => (
            <li key={ii}>{inlineMarkdown(item)}</li>
          ))}
        </ul>
      );
    }

    if (block.type === "ol") {
      return (
        <ol key={bi} className="chat-md-olist">
          {block.items.map((item, ii) => (
            <li key={ii}>{inlineMarkdown(item)}</li>
          ))}
        </ol>
      );
    }

    const parts = [];
    block.lines.forEach((line, li) => {
      if (li > 0) parts.push(<br key={`br-${bi}-${li}`} />);
      inlineMarkdown(line).forEach((node, ni) => {
        parts.push(React.isValidElement(node) ? React.cloneElement(node, { key: `${bi}-${li}-${ni}` }) : node);
      });
    });
    return <p key={bi} className="chat-md-p">{parts}</p>;
  });
}

function inlineMarkdown(text) {
  let safeText = text;
  const doubleAsteriskCount = (safeText.match(/\*\*/g) || []).length;
  if (doubleAsteriskCount % 2 === 1) {
    const lastIndex = safeText.lastIndexOf("**");
    if (lastIndex >= 0) {
      safeText = `${safeText.slice(0, lastIndex)}${safeText.slice(lastIndex + 2)}`;
    }
  }

  const parts = [];
  const re = /\*\*(.+?)\*\*|\*(.+?)\*|`([^`]+)`/g;
  let last = 0;
  let m;
  while ((m = re.exec(safeText)) !== null) {
    if (m.index > last) parts.push(safeText.slice(last, m.index));
    if (m[1] !== undefined) parts.push(<strong key={m.index}>{m[1]}</strong>);
    else if (m[2] !== undefined) parts.push(<em key={m.index}>{m[2]}</em>);
    else if (m[3] !== undefined) parts.push(<code key={m.index} className="chat-md-code">{m[3]}</code>);
    last = m.index + m[0].length;
  }
  if (last < safeText.length) parts.push(safeText.slice(last));
  return parts.length ? parts : [text];
}

const EXPLAIN_WITH_EVIDENCE_QUERY = "explain with evidence";
const CHAT_ACTIVE_SESSION_KEY = "automatisor_active_chat_v1";

function chatScopeKey(scope, siteId) {
  return scope === "facilities" ? "facility" : `site:${siteId}`;
}

function loadStoredChatSession(scopeKey) {
  if (!scopeKey) return "";
  try {
    const raw = window.sessionStorage.getItem(CHAT_ACTIVE_SESSION_KEY);
    const map = raw ? JSON.parse(raw) : {};
    return typeof map[scopeKey] === "string" ? map[scopeKey] : "";
  } catch {
    return "";
  }
}

function saveStoredChatSession(scopeKey, sessionId) {
  if (!scopeKey || !sessionId) return;
  try {
    const raw = window.sessionStorage.getItem(CHAT_ACTIVE_SESSION_KEY);
    const map = raw ? JSON.parse(raw) : {};
    map[scopeKey] = sessionId;
    window.sessionStorage.setItem(CHAT_ACTIVE_SESSION_KEY, JSON.stringify(map));
  } catch {
    // Ignore.
  }
}

function chatApiBase(chatType) {
  return chatType === "facility" ? "/api/chat/facilities" : "/api/chat";
}

function sessionMatchesPage(session, scope, siteId) {
  if (!session) return false;
  if (session.chat_type === "facility") return scope === "facilities";
  if (session.chat_type === "site") return scope === "site" && session.site_id === siteId;
  return false;
}

function pickDefaultSession(sessions, scope, siteId, preferredSessionId) {
  if (preferredSessionId) {
    const match = sessions.find((session) => session.session_id === preferredSessionId);
    if (match) return match.session_id;
  }
  const scoped = sessions.filter((session) => {
    if (scope === "facilities") return session.chat_type === "facility";
    return session.chat_type === "site" && session.site_id === siteId;
  });
  return scoped[0]?.session_id || null;
}


function formatSessionTitle(session) {
  if (session?.title) return session.title;
  const date = new Date(session?.created_at || Date.now());
  return `Chat - ${date.toLocaleDateString()} ${date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

function formatHistoryLabel(session) {
  if (session?.display_label) return session.display_label;
  return formatSessionTitle(session);
}

function formatHistoryTypeCol(session) {
  if (session?.chat_type === "facility") return "Facility";
  if (session?.display_label) {
    const idx = session.display_label.lastIndexOf(" - ");
    if (idx !== -1) {
      const suffix = session.display_label.slice(idx + 3);
      const parenIdx = suffix.indexOf("(");
      const name = parenIdx !== -1 ? suffix.slice(0, parenIdx).trim() : suffix.trim();
      if (name) return name;
    }
  }
  return "Site";
}

/**
 * ChatWidget - per-report or multi-facility AI chatbot panel.
 * Props:
 *   siteId (string) - the site UUID for single-report mode
 *   scope ("site" | "facilities") - page context; default "site"
 *   senderEmail (string) - authenticated user email for sharing
 *   companyName (string) - site company name for share emails
 */
export default function ChatWidget({
  siteId,
  scope = "site",
  senderEmail = "",
  companyName = "",
  hideHistory = false,
}) {
  const isFacilitiesScope = scope === "facilities";
  const navigate = useNavigate();
  const location = useLocation();
  const scopeKey = chatScopeKey(scope, siteId);
  const navSessionId = location.state?.chatSessionId || "";
  const storedSessionId = navSessionId ? "" : loadStoredChatSession(scopeKey);

  const preferredSessionIdRef = useRef(navSessionId || null);
  const userLockedSessionIdRef = useRef(storedSessionId || null);

  const [isOpen, setIsOpen] = useState(() => Boolean(navSessionId));
  const [isMaximized, setIsMaximized] = useState(() => Boolean(navSessionId));
  const [historyOpen, setHistoryOpen] = useState(false);
  const [sessions, setSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState(navSessionId || storedSessionId || null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [error, setError] = useState("");
  const [feedbackState, setFeedbackState] = useState({});
  const [showShareDialog, setShowShareDialog] = useState(false);

  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const historyRef = useRef(null);

  function commitUserSessionChoice(sessionId) {
    if (!sessionId) return;
    userLockedSessionIdRef.current = sessionId;
    preferredSessionIdRef.current = null;
    if (!hideHistory) saveStoredChatSession(scopeKey, sessionId);
  }

  useEffect(() => {
    const nextNavSessionId = location.state?.chatSessionId;
    if (!nextNavSessionId) return;
    preferredSessionIdRef.current = nextNavSessionId;
    userLockedSessionIdRef.current = null;
    setActiveSessionId(nextNavSessionId);
    setIsOpen(true);
    setIsMaximized(true);
  }, [location.state?.chatSessionId]);

  useEffect(() => {
    if (!activeSessionId || hideHistory || !scopeKey) return;
    if (userLockedSessionIdRef.current === activeSessionId) {
      saveStoredChatSession(scopeKey, activeSessionId);
    }
  }, [activeSessionId, hideHistory, scopeKey]);

  const activeSession = sessions.find((session) => session.session_id === activeSessionId) || null;
  const activeChatType = activeSession?.chat_type || (isFacilitiesScope ? "facility" : "site");
  const messageApiBase = chatApiBase(activeChatType);
  const canInteractWithSession = Boolean(
    activeSessionId && activeSession && sessionMatchesPage(activeSession, scope, siteId),
  );

  useEffect(() => {
    if (!historyOpen) return;
    function onClickOutside(e) {
      if (historyRef.current && !historyRef.current.contains(e.target)) {
        setHistoryOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [historyOpen]);

  async function createSessionForScope() {
    const createUrl = isFacilitiesScope ? "/api/chat/facilities/session" : "/api/chat/session";
    const createBody = isFacilitiesScope ? {} : { site_id: siteId };
    const res = await fetch(createUrl, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(createBody),
    });
    const data = await res.json();
    if (data.detail) throw new Error(data.detail);
    const newSession = {
      session_id: data.session_id,
      title: null,
      chat_type: isFacilitiesScope ? "facility" : "site",
      site_id: isFacilitiesScope ? null : siteId,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    return newSession;
  }

  useEffect(() => {
    if (!isOpen) return;
    if (!isFacilitiesScope && !siteId) return;

    let cancelled = false;
    setSessionsLoading(true);
    setError("");

    if (hideHistory) {
      // Ephemeral mode — always start fresh, skip history fetch entirely.
      userLockedSessionIdRef.current = null;
      preferredSessionIdRef.current = null;
      createSessionForScope()
        .then((newSession) => {
          if (cancelled) return;
          setSessions([newSession]);
          setActiveSessionId(newSession.session_id);
          setMessages([]);
        })
        .catch(() => {
          if (!cancelled) setError("Could not start a new chat.");
        })
        .finally(() => {
          if (!cancelled) setSessionsLoading(false);
        });
    } else {
      fetch("/api/chat/sessions", { credentials: "include" })
        .then((res) => res.json())
        .then(async (data) => {
          if (cancelled) return;
          if (data.detail) {
            setError(data.detail);
            return;
          }

          const list = data.sessions || [];
          setSessions(list);

          const lockedId = userLockedSessionIdRef.current;
          if (lockedId) {
            const lockedSession = list.find((session) => session.session_id === lockedId);
            if (lockedSession && sessionMatchesPage(lockedSession, scope, siteId)) {
              setActiveSessionId(lockedId);
              return;
            }
            userLockedSessionIdRef.current = null;
          }

          const preferredId = pickDefaultSession(list, scope, siteId, preferredSessionIdRef.current);
          if (preferredId) {
            preferredSessionIdRef.current = null;
            commitUserSessionChoice(preferredId);
            setActiveSessionId(preferredId);
            return;
          }

          try {
            const newSession = await createSessionForScope();
            if (cancelled) return;
            setSessions((prev) => [newSession, ...prev.filter((session) => session.session_id !== newSession.session_id)]);
            commitUserSessionChoice(newSession.session_id);
            setActiveSessionId(newSession.session_id);
          } catch {
            if (!cancelled) setError("Could not start a new chat.");
          }
        })
        .catch(() => {
          if (!cancelled) setError("Could not load chat sessions.");
        })
        .finally(() => {
          if (!cancelled) setSessionsLoading(false);
        });
    }

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, siteId, isFacilitiesScope, scope, hideHistory]);

  useEffect(() => {
    if (!activeSessionId) return;
    let cancelled = false;
    setHistoryLoading(true);
    setMessages([]);
    setError("");

    fetch(`/api/chat/history?session_id=${encodeURIComponent(activeSessionId)}`, {
      credentials: "include",
    })
      .then((res) => res.json())
      .then((data) => {
        if (cancelled) return;
        if (data.detail) {
          setError(data.detail);
          return;
        }
        setMessages(data.messages || []);
        if (data.session) {
          setSessions((prev) => {
            const exists = prev.some((session) => session.session_id === data.session.session_id);
            if (exists) {
              return prev.map((session) =>
                session.session_id === data.session.session_id ? { ...session, ...data.session } : session,
              );
            }
            return [data.session, ...prev];
          });
        }
      })
      .catch(() => {
        if (!cancelled) setError("Could not load conversation history.");
      })
      .finally(() => {
        if (!cancelled) setHistoryLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [activeSessionId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (isOpen) window.setTimeout(() => inputRef.current?.focus(), 50);
  }, [isOpen]);

  async function handleNewChat() {
    if (!isFacilitiesScope && !siteId) return;
    setError("");
    setHistoryOpen(false);
    try {
      const newSession = await createSessionForScope();
      setSessions((prev) => [newSession, ...prev]);
      commitUserSessionChoice(newSession.session_id);
      setActiveSessionId(newSession.session_id);
      setMessages([]);
    } catch {
      setError("Could not start a new chat.");
    }
  }

  function handleHistorySelect(session) {
    if (!session?.session_id) return;
    setHistoryOpen(false);

    if (!sessionMatchesPage(session, scope, siteId)) {
      if (session.chat_type === "facility") {
        navigate("/workspace", { state: { chatSessionId: session.session_id } });
      } else if (session.chat_type === "site" && session.site_id) {
        navigate("/workspace/report", {
          state: { siteId: session.site_id, chatSessionId: session.session_id },
        });
      }
      return;
    }

    setActiveSessionId(session.session_id);
    commitUserSessionChoice(session.session_id);
  }

  async function sendMessage(text) {
    const trimmed = String(text || "").trim();
    if (!trimmed || !activeSessionId || loading || !canInteractWithSession) return;

    const isFirst = messages.length === 0;
    const optimisticUser = { role: "user", content: trimmed, ts: new Date().toISOString() };
    setMessages((prev) => [...prev, optimisticUser]);
    setInput("");
    setLoading(true);
    setError("");

    try {
      const messageBody =
        activeChatType === "facility"
          ? { session_id: activeSessionId, message: trimmed }
          : { site_id: activeSession?.site_id || siteId, session_id: activeSessionId, message: trimmed };
      const res = await fetch(`${messageApiBase}/message`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(messageBody),
      });
      const data = await res.json();
      if (data.detail) {
        setError(data.detail);
        setMessages((prev) => prev.filter((message) => message !== optimisticUser));
        setInput(trimmed);
        return;
      }
      const assistantMessageId = data.assistant_message_id || `assistant-${Date.now()}`;
      setMessages((prev) => [
        ...prev,
        {
          id: assistantMessageId,
          role: "assistant",
          content: data.reply,
          ts: new Date().toISOString(),
          metadata: {},
        },
      ]);
      setSessions((prev) =>
        prev.map((session) =>
          session.session_id === activeSessionId
            ? {
                ...session,
                updated_at: new Date().toISOString(),
                title: isFirst && data.title ? data.title : session.title,
                display_label:
                  isFirst && data.title
                    ? formatHistoryLabel({ ...session, title: data.title })
                    : session.display_label,
              }
            : session,
        ),
      );
    } catch {
      setError("Failed to send message.");
      setMessages((prev) => prev.filter((message) => message !== optimisticUser));
      setInput(trimmed);
    } finally {
      setLoading(false);
    }
  }

  function handleSend(event) {
    event.preventDefault();
    sendMessage(input);
  }

  function handleExplainWithEvidence() {
    sendMessage(EXPLAIN_WITH_EVIDENCE_QUERY);
  }

  async function handleFeedback(messageId, feedback) {
    if (!activeSessionId || !messageId || !canInteractWithSession) return;
    setFeedbackState((prev) => ({ ...prev, [messageId]: feedback }));
    try {
      const feedbackBody =
        activeChatType === "facility"
          ? { session_id: activeSessionId, message_id: messageId, feedback }
          : {
              site_id: activeSession?.site_id || siteId,
              session_id: activeSessionId,
              message_id: messageId,
              feedback,
            };
      const res = await fetch(`${messageApiBase}/feedback`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(feedbackBody),
      });
      const data = await res.json();
      if (data.detail) throw new Error(data.detail);
    } catch {
      setError("Could not save feedback.");
      setFeedbackState((prev) => {
        const next = { ...prev };
        delete next[messageId];
        return next;
      });
    }
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSend(event);
    }
  }

  function getMessageFeedback(message) {
    return feedbackState[message.id] || message?.metadata?.feedback?.value || "";
  }

  const headerTitle = activeSession
    ? formatSessionTitle(activeSession)
    : isFacilitiesScope
      ? "Facilities Assistant"
      : "Report Assistant";
  const canShareConversation =
    activeChatType === "site" &&
    scope === "site" &&
    activeSession?.site_id === siteId &&
    Boolean(activeSessionId && messages.length > 0);
  const emptyStateMessage = "Hi, how can I help today?";

  if (!isFacilitiesScope && !siteId) return null;

  return (
    <div className={`chat-widget${isOpen ? " chat-widget-open" : ""}`}>
      <button
        className="chat-widget-toggle"
        type="button"
        aria-label={isOpen ? "Close assistant" : "Ask Automatisor"}
        onClick={() => {
          setIsOpen((open) => {
            if (open) {
              setIsMaximized(false);
              return false;
            }
            setIsMaximized(true);
            return true;
          });
        }}
      >
        {isOpen ? (
          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M18 6L6 18M6 6l12 12" strokeWidth="2" strokeLinecap="round" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path
              d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        )}
        <span className="chat-widget-toggle-label">{isOpen ? "Close" : "Ask Automatisor"}</span>
      </button>

      {isOpen && (
        <div
          className={`chat-widget-panel${isMaximized ? " chat-widget-panel-maximized" : ""}`}
          role="dialog"
          aria-label={isFacilitiesScope ? "Facilities assistant" : "Report assistant"}
          aria-expanded={isMaximized}
        >
          <div className="chat-widget-header">
            <div className="chat-widget-header-left">
              {!hideHistory && (
                <div className="chat-widget-history-wrap" ref={historyRef}>
                  <button
                    className="chat-widget-history-btn"
                    type="button"
                    aria-label="Chat history"
                    aria-expanded={historyOpen}
                    onClick={() => setHistoryOpen((open) => !open)}
                  >
                    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                      <circle cx="12" cy="12" r="9" strokeWidth="2" />
                      <path d="M12 7v5l3 3" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </button>

                  {historyOpen && (
                    <div className="chat-widget-history-dropdown">
                      <div className="chat-widget-history-header">
                        <span>History</span>
                        <button
                          className="chat-widget-history-new"
                          type="button"
                          onClick={handleNewChat}
                          disabled={loading}
                        >
                          + New chat
                        </button>
                      </div>
                      {sessionsLoading ? (
                        <p className="chat-widget-history-empty">Loading...</p>
                      ) : sessions.length === 0 ? (
                        <p className="chat-widget-history-empty">No previous chats.</p>
                      ) : (
                        <div className="chat-widget-history-table">
                          <div className="chat-widget-history-table-head">
                            <span>Title</span>
                            <span>Source</span>
                          </div>
                          <div className="chat-widget-history-table-body">
                            {sessions.map((session) => (
                              <button
                                key={session.session_id}
                                type="button"
                                className={`chat-widget-history-row${session.session_id === activeSessionId ? " active" : ""}`}
                                onClick={() => handleHistorySelect(session)}
                              >
                                <span className="chat-widget-history-col-title">
                                  {formatSessionTitle(session)}
                                </span>
                                <span className="chat-widget-history-col-type">
                                  {formatHistoryTypeCol(session)}
                                </span>
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              <span className="chat-widget-title" title={headerTitle}>{headerTitle}</span>
            </div>

            <div className="chat-widget-header-actions">
              {canShareConversation ? (
                <button
                  className="chat-widget-icon-btn"
                  type="button"
                  aria-label="Share conversation"
                  title="Share conversation"
                  onClick={() => setShowShareDialog(true)}
                  disabled={loading}
                >
                  <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <path d="M4 12v7a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-7" strokeWidth="2" strokeLinecap="round" />
                    <path d="M16 6l-4-4-4 4" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    <path d="M12 2v14" strokeWidth="2" strokeLinecap="round" />
                  </svg>
                </button>
              ) : null}
              <button
                className="chat-widget-icon-btn"
                type="button"
                aria-label={isMaximized ? "Restore chat size" : "Maximize chat"}
                title={isMaximized ? "Restore" : "Maximize"}
                onClick={() => setIsMaximized((maximized) => !maximized)}
              >
                {isMaximized ? (
                  <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <path d="M8 4H4v4" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    <path d="M4 4l6 6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    <path d="M16 20h4v-4" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    <path d="M20 20l-6-6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                ) : (
                  <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <path d="M15 4h5v5" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    <path d="M9 20H4v-5" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    <path d="M14 10l6-6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    <path d="M4 20l6-6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </button>

              <button
                className="chat-widget-new-btn"
                type="button"
                onClick={handleNewChat}
                disabled={loading}
              >
                + New
              </button>
            </div>
          </div>

          <div className="chat-widget-messages" aria-live="polite" aria-atomic="false">
            {historyLoading ? (
              <p className="chat-widget-status">Loading conversation...</p>
            ) : messages.length === 0 && !error ? (
              <p className="chat-widget-status">{emptyStateMessage}</p>
            ) : (
              messages.map((msg, i) => (
                <div key={msg.id || i} className={`chat-widget-message chat-widget-message-${msg.role}`}>
                  <span className="chat-widget-message-role">
                    {msg.role === "user" ? "You" : "Automatisor"}
                  </span>
                  {msg.role === "assistant" ? (
                    <>
                      <div className="chat-widget-message-content chat-md">{renderMarkdown(msg.content)}</div>
                      <div className="chat-widget-feedback-row" aria-label="Feedback controls">
                        <button
                          type="button"
                          className={`chat-widget-feedback-btn${getMessageFeedback(msg) === "up" ? " active" : ""}`}
                          aria-label="Helpful response"
                          title="Helpful"
                          onClick={() => handleFeedback(msg.id, "up")}
                          disabled={!msg.id || !canInteractWithSession}
                        >
                          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                            <path d="M7 11v9H4v-9h3zm4 9h6.6a2 2 0 0 0 2-1.6l1.2-7a2 2 0 0 0-2-2.4H13l1-4.8a2 2 0 0 0-2-2.4L7 11v9h4z" strokeWidth="2" strokeLinejoin="round" />
                          </svg>
                        </button>
                        <button
                          type="button"
                          className={`chat-widget-feedback-btn${getMessageFeedback(msg) === "down" ? " active" : ""}`}
                          aria-label="Unhelpful response"
                          title="Not helpful"
                          onClick={() => handleFeedback(msg.id, "down")}
                          disabled={!msg.id || !canInteractWithSession}
                        >
                          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                            <path d="M7 13V4H4v9h3zm4-9h6.6a2 2 0 0 1 2 1.6l1.2 7a2 2 0 0 1-2 2.4H13l1 4.8a2 2 0 0 1-2 2.4L7 13V4h4z" strokeWidth="2" strokeLinejoin="round" />
                          </svg>
                        </button>
                        <button
                          type="button"
                          className="chat-widget-feedback-btn chat-widget-evidence-btn"
                          aria-label="Explain with evidence"
                          data-tooltip={EXPLAIN_WITH_EVIDENCE_QUERY}
                          onClick={handleExplainWithEvidence}
                          disabled={loading || !activeSessionId || !canInteractWithSession}
                        >
                          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                            <circle cx="12" cy="12" r="9" strokeWidth="2" />
                            <path d="M12 10v6" strokeWidth="2" strokeLinecap="round" />
                            <circle cx="12" cy="7.25" r="1.1" fill="currentColor" stroke="none" />
                          </svg>
                        </button>
                      </div>
                    </>
                  ) : (
                    <p className="chat-widget-message-content">{msg.content}</p>
                  )}
                </div>
              ))
            )}
            <div ref={messagesEndRef} />
          </div>

          {error && <p className="chat-widget-error">{error}</p>}

          {loading && (
            <div className="chat-widget-typing-row" aria-live="polite">
              <span className="chat-widget-typing-label">Automatisor is thinking...</span>
            </div>
          )}

          <form className="chat-widget-form" onSubmit={handleSend}>
            <textarea
              ref={inputRef}
              className="chat-widget-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={isFacilitiesScope ? "Ask about your facilities..." : "Type a question..."}
              rows={2}
              maxLength={2000}
              disabled={loading || !activeSessionId || !canInteractWithSession}
              aria-label="Message input"
            />
            <button
              className="chat-widget-send-btn"
              type="submit"
              disabled={loading || !input.trim() || !activeSessionId || !canInteractWithSession}
              aria-label="Send message"
            >
              Send
            </button>
          </form>
        </div>
      )}

      {showShareDialog && canShareConversation
        ? createPortal(
            <ShareChatDialog
              siteId={siteId}
              sessionId={activeSessionId}
              chatTitle={headerTitle}
              companyName={companyName}
              senderEmail={senderEmail}
              onClose={() => setShowShareDialog(false)}
            />,
            document.body,
          )
        : null}
    </div>
  );
}
