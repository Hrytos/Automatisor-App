import React, { useEffect, useRef, useState } from "react";

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
      // Treat non-marker lines after a marker as wrapped list content.
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
  // Guard against a dangling final bold marker like "...value**".
  let safeText = text;
  const doubleAsteriskCount = (safeText.match(/\*\*/g) || []).length;
  if (doubleAsteriskCount % 2 === 1) {
    const lastIndex = safeText.lastIndexOf("**");
    if (lastIndex >= 0) {
      safeText = `${safeText.slice(0, lastIndex)}${safeText.slice(lastIndex + 2)}`;
    }
  }

  // Tokenise **bold**, *italic*, `code`
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

/**
 * ChatWidget - per-report AI chatbot panel.
 * Props:
 *   siteId (string) - the site UUID for the active report view
 */
export default function ChatWidget({ siteId }) {
  const [isOpen, setIsOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [sessions, setSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [error, setError] = useState("");

  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const historyRef = useRef(null);

  // Close history dropdown when clicking outside
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

  // Fetch sessions when the panel opens or siteId changes
  useEffect(() => {
    if (!isOpen || !siteId) return;
    let cancelled = false;
    setSessionsLoading(true);
    setError("");
    fetch(`/api/chat/sessions?site_id=${encodeURIComponent(siteId)}`, {
      credentials: "include",
    })
      .then((res) => res.json())
      .then((data) => {
        if (cancelled) return;
        if (data.detail) { setError(data.detail); return; }
        const list = data.sessions || [];
        setSessions(list);
        if (list.length > 0 && !activeSessionId) {
          setActiveSessionId(list[0].session_id);
        } else if (list.length === 0) {
          // No sessions yet — auto-create one so the input is ready immediately
          fetch("/api/chat/session", {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ site_id: siteId }),
          })
            .then((r) => r.json())
            .then((d) => {
              if (cancelled || d.detail) return;
              const newSession = {
                session_id: d.session_id,
                title: null,
                created_at: new Date().toISOString(),
                updated_at: new Date().toISOString(),
              };
              setSessions([newSession]);
              setActiveSessionId(d.session_id);
            })
            .catch(() => {});
        }
      })
      .catch(() => { if (!cancelled) setError("Could not load chat sessions."); })
      .finally(() => { if (!cancelled) setSessionsLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, siteId]);

  // Fetch history when activeSessionId changes
  useEffect(() => {
    if (!activeSessionId || !siteId) return;
    let cancelled = false;
    setHistoryLoading(true);
    setMessages([]);
    setError("");
    fetch(
      `/api/chat/history?site_id=${encodeURIComponent(siteId)}&session_id=${encodeURIComponent(activeSessionId)}`,
      { credentials: "include" },
    )
      .then((res) => res.json())
      .then((data) => {
        if (cancelled) return;
        if (data.detail) { setError(data.detail); return; }
        setMessages(data.messages || []);
      })
      .catch(() => { if (!cancelled) setError("Could not load conversation history."); })
      .finally(() => { if (!cancelled) setHistoryLoading(false); });
    return () => { cancelled = true; };
  }, [activeSessionId, siteId]);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Focus input when panel opens
  useEffect(() => {
    if (isOpen) window.setTimeout(() => inputRef.current?.focus(), 50);
  }, [isOpen]);

  async function handleNewChat() {
    if (!siteId) return;
    setError("");
    setHistoryOpen(false);
    try {
      const res = await fetch("/api/chat/session", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ site_id: siteId }),
      });
      const data = await res.json();
      if (data.detail) { setError(data.detail); return; }
      const newSession = {
        session_id: data.session_id,
        title: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      setSessions((prev) => [newSession, ...prev]);
      setActiveSessionId(data.session_id);
      setMessages([]);
    } catch {
      setError("Could not start a new chat.");
    }
  }

  function switchSession(sessionId) {
    setActiveSessionId(sessionId);
    setHistoryOpen(false);
  }

  async function handleSend(event) {
    event.preventDefault();
    const text = input.trim();
    if (!text || !activeSessionId || loading) return;

    const isFirst = messages.length === 0;
    const optimisticUser = { role: "user", content: text, ts: new Date().toISOString() };
    setMessages((prev) => [...prev, optimisticUser]);
    setInput("");
    setLoading(true);
    setError("");

    try {
      const res = await fetch("/api/chat/message", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ site_id: siteId, session_id: activeSessionId, message: text }),
      });
      const data = await res.json();
      if (data.detail) {
        setError(data.detail);
        setMessages((prev) => prev.filter((m) => m !== optimisticUser));
        setInput(text);
        return;
      }
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: data.reply, ts: new Date().toISOString() },
      ]);
      // Update session in list: set title on first message, bump updated_at always
      setSessions((prev) =>
        prev.map((s) =>
          s.session_id === activeSessionId
            ? {
                ...s,
                updated_at: new Date().toISOString(),
                title: isFirst && data.title ? data.title : s.title,
              }
            : s,
        ),
      );
    } catch {
      setError("Failed to send message.");
      setMessages((prev) => prev.filter((m) => m !== optimisticUser));
      setInput(text);
    } finally {
      setLoading(false);
    }
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSend(event);
    }
  }

  function formatSessionTitle(session) {
    if (session.title) return session.title;
    const date = new Date(session.created_at);
    return `Chat - ${date.toLocaleDateString()} ${date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  }

  // Title shown in the header for the active session
  const activeSession = sessions.find((s) => s.session_id === activeSessionId);
  const headerTitle = activeSession ? formatSessionTitle(activeSession) : "Report Assistant";

  if (!siteId) return null;

  return (
    <div className={`chat-widget${isOpen ? " chat-widget-open" : ""}`}>
      {/* Toggle button */}
      <button
        className="chat-widget-toggle"
        type="button"
        aria-label={isOpen ? "Close report assistant" : "Open report assistant"}
        onClick={() => setIsOpen((v) => !v)}
      >
        {isOpen ? (
          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M18 6L6 18M6 6l12 12" strokeWidth="2" strokeLinecap="round" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path
              d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            />
          </svg>
        )}
        <span className="chat-widget-toggle-label">{isOpen ? "Close" : "Ask AI"}</span>
      </button>

      {/* Panel */}
      {isOpen && (
        <div className="chat-widget-panel" role="dialog" aria-label="Report assistant">
          {/* Header */}
          <div className="chat-widget-header">
            <div className="chat-widget-header-left">
              {/* Clock / history button */}
              <div className="chat-widget-history-wrap" ref={historyRef}>
                <button
                  className="chat-widget-history-btn"
                  type="button"
                  aria-label="Chat history"
                  aria-expanded={historyOpen}
                  onClick={() => setHistoryOpen((v) => !v)}
                >
                  {/* Clock icon */}
                  <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <circle cx="12" cy="12" r="9" strokeWidth="2" />
                    <path d="M12 7v5l3 3" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>

                {/* History dropdown */}
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
                      <ul className="chat-widget-history-list">
                        {sessions.map((session) => (
                          <li key={session.session_id}>
                            <button
                              type="button"
                              className={`chat-widget-history-item${session.session_id === activeSessionId ? " active" : ""}`}
                              onClick={() => switchSession(session.session_id)}
                            >
                              <span className="chat-widget-history-item-title">
                                {formatSessionTitle(session)}
                              </span>
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </div>

              <span className="chat-widget-title" title={headerTitle}>{headerTitle}</span>
            </div>

            <button
              className="chat-widget-new-btn"
              type="button"
              onClick={handleNewChat}
              disabled={loading}
            >
              + New
            </button>
          </div>

          {/* Messages */}
          <div className="chat-widget-messages" aria-live="polite" aria-atomic="false">
            {historyLoading ? (
              <p className="chat-widget-status">Loading conversation...</p>
            ) : messages.length === 0 && !error ? (
              <p className="chat-widget-status">Ask anything about this report.</p>
            ) : (
              messages.map((msg, i) => (
                <div key={i} className={`chat-widget-message chat-widget-message-${msg.role}`}>
                  <span className="chat-widget-message-role">
                    {msg.role === "user" ? "You" : "AI"}
                  </span>
                  {msg.role === "assistant"
                    ? <div className="chat-widget-message-content chat-md">{renderMarkdown(msg.content)}</div>
                    : <p className="chat-widget-message-content">{msg.content}</p>
                  }
                </div>
              ))
            )}
            {loading && (
              <div className="chat-widget-message chat-widget-message-assistant">
                <span className="chat-widget-message-role">AI</span>
                <p className="chat-widget-message-content chat-widget-typing">thinking</p>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Error */}
          {error && <p className="chat-widget-error">{error}</p>}

          {/* Input */}
          <form className="chat-widget-form" onSubmit={handleSend}>
            <textarea
              ref={inputRef}
              className="chat-widget-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Type a question..."
              rows={2}
              maxLength={2000}
              disabled={loading || !activeSessionId}
              aria-label="Message input"
            />
            <button
              className="chat-widget-send-btn"
              type="submit"
              disabled={loading || !input.trim() || !activeSessionId}
              aria-label="Send message"
            >
              {loading ? "thinking" : "Send"}
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
