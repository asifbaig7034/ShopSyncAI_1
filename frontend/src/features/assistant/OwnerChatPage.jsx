import { useEffect, useRef, useState } from 'react';
import { getOwnerHistory, sendOwnerRequest } from '../../api/client.js';
import SettingsPanel from '../setup/SettingsPanel.jsx';
import ConversationsPage from '../inbox/ConversationsPage.jsx';
import OrdersPage from '../orders/OrdersPage.jsx';

const conversationKey = (ownerId) => `shopsync-uc2-conversation-id:${ownerId}`;
const EXAMPLES = [
  'Show products and low stock',
  'Show recent orders',
  'Show me',
];

function savedConversationId(ownerId) {
  try { return sessionStorage.getItem(conversationKey(ownerId)) || ''; }
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

export default function OwnerChatPage({ session, onLogout, onProfileSaved }) {
  const token = session.access_token;
  const [conversationId, setConversationId] = useState(() => savedConversationId(session.owner.id));
  const [message, setMessage] = useState('');
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [notice, setNotice] = useState('');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [page, setPage] = useState('assistant');
  const [savingSettings, setSavingSettings] = useState(false);
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

  function resetConversation() {
    historyRevision.current += 1;
    justCreatedConversation.current = '';
    try { sessionStorage.removeItem(conversationKey(session.owner.id)); } catch { /* Storage is optional. */ }
    setConversationId('');
    setMessages([]);
    setMessage('');
    setNotice('');
  }

  function navigate(next) {
    setPage(next);
    setSettingsOpen(false);
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
        try { sessionStorage.setItem(conversationKey(session.owner.id), data.conversation_id); } catch { /* Storage is optional. */ }
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
        <div className="brand brand--logo"><img src="/shopsync-logo.png" alt="ShopSync AI" /></div>
        <span className="header-context">Owner workspace</span>
      </header>

      <main className="workspace">
        <nav className="workspace-tabs" aria-label="Owner workspace navigation">
          <button type="button" aria-current={page === 'assistant' ? 'page' : undefined} onClick={() => navigate('assistant')}>Owner chat</button>
          <button type="button" aria-current={page === 'inbox' ? 'page' : undefined} onClick={() => navigate('inbox')}>Customer inbox</button>
          <button type="button" aria-current={page === 'orders' ? 'page' : undefined} onClick={() => navigate('orders')}>Orders</button>
        </nav>
        <section className="intro" aria-labelledby="page-title">
          <div className="eyebrow">{settingsOpen ? 'OWNER SETTINGS' : page === 'inbox' ? 'CUSTOMER CONVERSATIONS' : page === 'orders' ? 'ORDER MANAGEMENT' : 'AGENT ORCHESTRATION'}</div>
          <h1 id="page-title">{settingsOpen ? 'Set up your brand.' : page === 'inbox' ? 'Talk with your customers.' : page === 'orders' ? 'Manage your orders.' : 'Ask your business a question.'}</h1>
          <p>{settingsOpen ? 'Update your business profile and choose the agents available to answer owner requests.' : page === 'inbox' ? 'See customer messages, take over a conversation and try the local simulator.' : page === 'orders' ? 'Create products and orders, check risk holds and review return requests.' : 'Your request is routed to available agent handlers, and their results come back in one answer.'}</p>
        </section>

        <div className="workspace-grid">
          <aside className="side-panel" aria-label="Owner and demo guide">
            <div className="panel-label">SIGNED IN</div>
            <h2>{session.owner.business_name}</h2>
            <p>{session.owner.name}<br />{session.owner.email}</p>
            <button className="button button--secondary" type="button" onClick={onLogout} disabled={busy || savingSettings}>Log out</button>
            <div className="connection-status" role="status"><span className="status-dot status-dot--set" />Owner account active</div>
            <div className="sidebar-divider" />
            <div className="panel-label">FOR YOUR FYP DEMO</div>
            <ul className="guide-list">
              <li>Use fictional product and order data.</li>
              <li>AI classification runs only when configured.</li>
              <li>WhatsApp read access can be checked in Settings; live customer messaging belongs to UC-3.</li>
            </ul>
            <p className="privacy-note">Use fictional prompts for this FYP demo. Local Ollama needs no cloud key; do not enter a real customer’s personal information.</p>
          </aside>

          <section className="chat-panel" aria-label="Owner chat">
            <div className="chat-header">
              <div><div className="panel-label">{settingsOpen ? 'ACCOUNT SETUP' : page === 'inbox' ? 'CUSTOMER SUPPORT' : page === 'orders' ? 'COMMERCE' : 'OWNER CHAT'}</div><h2>{settingsOpen ? 'Settings' : page === 'inbox' ? 'Inbox' : page === 'orders' ? 'Orders' : 'Orchestrator'}</h2></div>
              <div className="chat-header__actions">
                <button className="button button--quiet" type="button" onClick={() => setSettingsOpen(!settingsOpen)} disabled={busy || savingSettings}>{settingsOpen ? 'Back to workspace' : 'Settings'}</button>
                {!settingsOpen && page === 'assistant' && <button className="button button--quiet" type="button" onClick={resetConversation} disabled={busy}>New chat</button>}
              </div>
            </div>
            {settingsOpen ? <SettingsPanel token={token} onProfileSaved={onProfileSaved} onSavingChange={setSavingSettings} /> : page === 'inbox' ? <ConversationsPage token={token} /> : page === 'orders' ? <OrdersPage token={token} /> : <>
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
                <textarea id="owner-message" rows="2" maxLength="2000" value={message} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); if (!busy && !loadingHistory) event.currentTarget.form?.requestSubmit(); } }} placeholder="Ask about your products, stock, or orders…" disabled={busy || loadingHistory} />
                <button type="submit" className="button button--primary" disabled={!message.trim() || busy || loadingHistory}>Send <span aria-hidden="true">↗</span></button>
              </div>
              <div className="composer__hint">Enter to send · Shift+Enter for a new line · Results use saved demonstration data</div>
              </form>
            </>}
          </section>
        </div>
      </main>
    </div>
  );
}
