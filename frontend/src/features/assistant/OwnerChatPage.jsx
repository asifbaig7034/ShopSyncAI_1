import { useEffect, useRef, useState } from 'react';
import { getOwnerHistory, sendOwnerRequest } from '../../api/client.js';

const SESSION_KEY = 'shopsync-uc2-conversation-id';
const EXAMPLES = [
  'Show products and low stock',
  'Show recent orders',
  'Show me',
];

function savedConversationId() {
  try { return sessionStorage.getItem(SESSION_KEY) || ''; }
  catch { return ''; }
}

function shortTime(date) {
  if (!date) return '';
  const value = new Date(date);
  return Number.isNaN(value.getTime()) ? '' : value.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function historyToMessages(history) {
  return (history || []).flatMap((entry, index) => [
    { id: `old-owner-${index}`, who: 'owner', text: String(entry.request || ''), at: entry.created_at },
    { id: `old-system-${index}`, who: 'system', text: typeof entry.result === 'string' ? entry.result : String(entry.result?.reply || ''), at: entry.created_at, mode: entry.mode, agents: entry.routed_agents || [], warnings: entry.warnings || [], approvalId: entry.approval_request_id },
  ]);
}

function modeLabel(mode) {
  if (mode === 'gemini') return 'Gemini AI';
  if (mode === 'ollama') return 'Local Ollama AI';
  return 'Local rules demo';
}

function ChatMessage({ item }) {
  return (
    <article className={`message message--${item.who}`}>
      <div className="message__meta">
        <span>{item.who === 'owner' ? 'You' : 'ShopSync AI'}</span>
        {item.at && <time dateTime={item.at}>{shortTime(item.at)}</time>}
      </div>
      <div className="message__body" dir="auto">{item.text}</div>
      {item.who === 'system' && !item.error && (
        <div className="message__details">
          <span className={`badge ${['gemini', 'ollama'].includes(item.mode) ? 'badge--ai' : 'badge--demo'}`}>{modeLabel(item.mode)}</span>
          {item.agents?.length > 0 && <span className="message__agents">Routed: {item.agents.join(', ')}</span>}
        </div>
      )}
      {item.warnings?.length > 0 && <div className="message__warning" role="note">{item.warnings.join(' · ')}</div>}
      {item.approvalId && <div className="message__approval" role="note">Owner approval pending · {item.approvalId}</div>}
    </article>
  );
}

export default function OwnerChatPage() {
  const [tokenDraft, setTokenDraft] = useState('');
  const [token, setToken] = useState('');
  const [conversationId, setConversationId] = useState(savedConversationId);
  const [message, setMessage] = useState('');
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [notice, setNotice] = useState('');
  const bottomRef = useRef(null);
  const justCreatedConversation = useRef('');
  const historyRevision = useRef(0);

  useEffect(() => {
    if (!token || !conversationId) return undefined;
    // The first reply is already shown. Reloading on its newly assigned ID
    // can overwrite the reply with an older, in-flight history response.
    if (conversationId === justCreatedConversation.current) {
      justCreatedConversation.current = '';
      return undefined;
    }
    let active = true;
    const revision = historyRevision.current;
    setLoadingHistory(true);
    getOwnerHistory({ token, conversationId })
      .then((data) => {
        if (active && revision === historyRevision.current) {
          setMessages(historyToMessages(data.messages));
          setNotice('');
        }
      })
      .catch((error) => { if (active) setNotice(error.message); })
      .finally(() => { if (active) setLoadingHistory(false); });
    return () => { active = false; };
  }, [token, conversationId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [messages, busy]);

  function useToken(event) {
    event.preventDefault();
    historyRevision.current += 1;
    setToken(tokenDraft.trim());
    setNotice('');
  }

  function resetConversation() {
    historyRevision.current += 1;
    justCreatedConversation.current = '';
    try { sessionStorage.removeItem(SESSION_KEY); } catch { /* Storage is optional. */ }
    setConversationId('');
    setMessages([]);
    setMessage('');
    setNotice('');
  }

  async function send(event) {
    event.preventDefault();
    const text = message.trim();
    if (!token || !text || busy || loadingHistory) return;
    historyRevision.current += 1;
    const ownerItem = { id: `owner-${Date.now()}`, who: 'owner', text, at: new Date().toISOString() };
    setMessages((items) => [...items, ownerItem]);
    setMessage('');
    setNotice('');
    setBusy(true);
    try {
      const data = await sendOwnerRequest({ token, message: text, conversationId });
      if (data.conversation_id && data.conversation_id !== conversationId) {
        justCreatedConversation.current = data.conversation_id;
        try { sessionStorage.setItem(SESSION_KEY, data.conversation_id); } catch { /* Storage is optional. */ }
        setConversationId(data.conversation_id);
      }
      setMessages((items) => [...items, {
        id: `reply-${Date.now()}`,
        who: 'system',
        text: data.reply || 'No answer was returned.',
        at: new Date().toISOString(),
        mode: data.mode,
        agents: data.routed_agents || [],
        warnings: data.warnings || [],
        approvalId: data.approval_request_id,
      }]);
    } catch (error) {
      setMessages((items) => [...items, { id: `error-${Date.now()}`, who: 'system', text: error.message, error: true, at: new Date().toISOString() }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="site-header">
        <div className="brand"><span className="brand__mark" aria-hidden="true">S</span><span>ShopSync <strong>AI</strong></span></div>
        <span className="header-context">UC-2 · Owner workspace</span>
      </header>

      <main className="workspace">
        <section className="intro" aria-labelledby="page-title">
          <div className="eyebrow">AGENT ORCHESTRATION</div>
          <h1 id="page-title">Ask your business a question.</h1>
          <p>Your request is routed to available agent handlers, and their results come back in one answer.</p>
        </section>

        <div className="workspace-grid">
          <aside className="side-panel" aria-label="Local setup and demo guide">
            <div className="panel-label">LOCAL SETUP</div>
            <h2>Owner access</h2>
            <p>Until UC-1 login is ready, enter the local demo token from your backend configuration.</p>
            <form onSubmit={useToken} className="token-form">
              <label htmlFor="owner-token">Demo owner token</label>
              <input id="owner-token" type="password" autoComplete="off" value={tokenDraft} onChange={(event) => setTokenDraft(event.target.value)} placeholder="Enter local token" />
              <button type="submit" className="button button--secondary" disabled={!tokenDraft.trim()}>{token ? 'Update token' : 'Use token'}</button>
            </form>
            <div className="connection-status" role="status"><span className={token ? 'status-dot status-dot--set' : 'status-dot'} />{token ? 'Token set for this tab' : 'Token required to chat'}</div>
            <div className="sidebar-divider" />
            <div className="panel-label">FOR YOUR FYP DEMO</div>
            <ul className="guide-list">
              <li>Use fictional product and order data.</li>
              <li>AI classification runs only when configured.</li>
              <li>Actual agent integrations belong to later use cases.</li>
            </ul>
            <p className="privacy-note">Use fictional prompts for this FYP demo. Local Ollama needs no cloud key; do not enter a real customer’s personal information.</p>
          </aside>

          <section className="chat-panel" aria-label="Owner chat">
            <div className="chat-header">
              <div><div className="panel-label">OWNER CHAT</div><h2>Orchestrator</h2></div>
              <button className="button button--quiet" type="button" onClick={resetConversation} disabled={busy}>New chat</button>
            </div>
            {notice && <div className="notice" role="alert">{notice}</div>}
            <div className="chat-messages" aria-live="polite" aria-busy={busy || loadingHistory}>
              {loadingHistory && <div className="loading">Loading saved conversation…</div>}
              {!loadingHistory && messages.length === 0 && (
                <div className="empty-chat">
                  <div className="empty-chat__icon" aria-hidden="true">✦</div>
                  <h3>What would you like to know?</h3>
                  <p>Try a synthetic request, or ask about the saved demonstration products and orders.</p>
                  <div className="prompt-list">
                    {EXAMPLES.map((example) => <button type="button" key={example} onClick={() => setMessage(example)}>{example}<span aria-hidden="true">↗</span></button>)}
                  </div>
                </div>
              )}
              {messages.map((item) => <ChatMessage key={item.id} item={item} />)}
              {busy && <div className="loading" role="status">Coordinating available agents…</div>}
              <div ref={bottomRef} />
            </div>
            <form className="composer" onSubmit={send}>
              <label htmlFor="owner-message">Your request</label>
              <div className="composer__row">
                <textarea id="owner-message" rows="2" maxLength="2000" value={message} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); if (!busy && !loadingHistory) event.currentTarget.form?.requestSubmit(); } }} placeholder={token ? 'Ask about your products, stock, or orders…' : 'Enter your demo token first…'} disabled={!token || busy || loadingHistory} />
                <button type="submit" className="button button--primary" disabled={!token || !message.trim() || busy || loadingHistory}>Send <span aria-hidden="true">↗</span></button>
              </div>
              <div className="composer__hint">Enter to send · Shift+Enter for a new line · Results use saved demonstration data</div>
            </form>
          </section>
        </div>
      </main>
    </div>
  );
}
