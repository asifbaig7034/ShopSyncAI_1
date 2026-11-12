import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { API_BASE, request } from './api';
import './styles.css';

const EXAMPLES = [
  { label: 'English', text: 'What products are available?' },
  { label: 'اردو', text: 'کون سی مصنوعات دستیاب ہیں؟' },
  { label: 'Roman Urdu', text: 'Kon se products available hain?' },
];

function Icon({ name, size = 20, ...props }) {
  const paths = {
    chat: <><path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z" /><path d="M8 11h8M8 14h5" /></>,
    arrow: <><path d="m5 12 14 0m-6-6 6 6-6 6" /></>,
    send: <><path d="m22 2-7 20-4-9-9-4 20-7Z" /><path d="m22 2-11 11" /></>,
    user: <><circle cx="12" cy="8" r="4" /><path d="M4 21v-2a8 8 0 0 1 16 0v2" /></>,
    bot: <><rect x="4" y="7" width="16" height="13" rx="4" /><path d="M12 3v4m-4 6h.01M16 13h.01M9 17h6M2 12v3m20-3v3" /></>,
    refresh: <><path d="M20 7a8 8 0 1 0 0 10M20 3v5h-5" /></>,
    lock: <><rect x="5" y="10" width="14" height="11" rx="3" /><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    package: <><path d="m12 2 9 5v10l-9 5-9-5V7l9-5Z" /><path d="m3 7 9 5 9-5M12 12v10M7.5 4.5l9 5" /></>,
    bolt: <path d="m13 2-9 12h7l-1 8 10-12h-7l1-8Z" />,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name] || paths.chat}</svg>;
}

function formatTime(value, includeDate = false) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', ...(includeDate ? { month: 'short', day: 'numeric' } : {}) }).format(date);
}

function lastText(conversation) {
  const value = conversation.last_message;
  return typeof value === 'string' ? value : value?.text || 'No messages yet';
}

function initials(value) {
  return String(value || '?').replace(/[^\p{L}\p{N} ]/gu, '').slice(0, 2).toUpperCase() || '?';
}

function productPrice(product) {
  if (product.price === null || product.price === undefined || product.price === '') return 'Price unavailable';
  const price = typeof product.price === 'number' ? product.price.toLocaleString() : String(product.price);
  return `${product.currency || ''} ${price}`.trim();
}

function productStock(product) {
  return product.stock !== null && product.stock !== undefined && Number.isFinite(Number(product.stock))
    ? `${product.stock} in stock` : 'Stock unavailable';
}

function ModeBadge({ mode }) {
  return <span className={`mode-badge ${mode === 'human' ? 'human' : 'bot'}`}><span />{mode === 'human' ? 'Owner handling' : 'Agent active'}</span>;
}

function App() {
  const [tokenInput, setTokenInput] = useState('');
  const [token, setToken] = useState('');
  const [connecting, setConnecting] = useState(false);
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState('');
  const [conversations, setConversations] = useState([]);
  const [conversation, setConversation] = useState(null);
  const [products, setProducts] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [search, setSearch] = useState('');
  const [error, setError] = useState('');
  const [loadingChat, setLoadingChat] = useState(false);
  const [busy, setBusy] = useState('');
  const [ownerText, setOwnerText] = useState('');
  const [customerId, setCustomerId] = useState('demo-customer');
  const [customerText, setCustomerText] = useState('');
  const [notice, setNotice] = useState('');
  const selectedRef = useRef(null);
  const demoRequest = useRef(null);
  const ownerRequest = useRef(null);
  const revision = useRef(0);
  const scrollEnd = useRef(null);
  const busyRef = useRef(false);
  const refreshRef = useRef(false);

  const fetchHealth = useCallback(async () => {
    try { setHealth(await request('/health')); setHealthError(''); }
    catch (err) { setHealthError(err.message); }
  }, []);

  useEffect(() => { fetchHealth(); }, [fetchHealth]);
  useEffect(() => { scrollEnd.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }, [conversation?.messages?.length, selectedId]);

  function selectConversation(id) {
    revision.current += 1;
    selectedRef.current = id;
    setSelectedId(id);
    setConversation(null);
    setOwnerText('');
  }

  const refresh = useCallback(async (quiet = false) => {
    if (!token || refreshRef.current || busyRef.current) return;
    refreshRef.current = true;
    const currentRevision = revision.current;
    try {
      const list = await request('/conversations', { token });
      if (revision.current !== currentRevision) return;
      setConversations(list.items || []);
      const id = selectedRef.current;
      if (id) {
        const result = await request(`/conversations/${encodeURIComponent(id)}`, { token });
        if (selectedRef.current === id && !busyRef.current && revision.current === currentRevision) setConversation(result.conversation);
      }
      if (!quiet) { setError(''); await fetchHealth(); }
    } catch (err) { if (revision.current === currentRevision) setError(err.message); }
    finally { refreshRef.current = false; }
  }, [token, fetchHealth]);

  useEffect(() => {
    if (!token) return undefined;
    const interval = setInterval(() => refresh(true), 5000);
    return () => clearInterval(interval);
  }, [token, refresh]);

  useEffect(() => {
    if (!selectedId || !token) return undefined;
    const controller = new AbortController();
    const currentRevision = revision.current;
    setLoadingChat(true);
    request(`/conversations/${encodeURIComponent(selectedId)}`, { token, signal: controller.signal })
      .then((data) => { if (selectedRef.current === selectedId && revision.current === currentRevision) setConversation(data.conversation); })
      .catch((err) => { if (!controller.signal.aborted && revision.current === currentRevision) setError(err.message); })
      .finally(() => { if (!controller.signal.aborted) setLoadingChat(false); });
    return () => controller.abort();
  }, [selectedId, token]);

  async function connect(event) {
    event.preventDefault();
    const nextToken = tokenInput.trim();
    if (!nextToken || connecting) return;
    setConnecting(true); setError('');
    try {
      const [list, catalogue] = await Promise.all([request('/conversations', { token: nextToken }), request('/products', { token: nextToken })]);
      setToken(nextToken); setTokenInput(''); setConversations(list.items || []); setProducts(catalogue.items || []);
      if (list.items?.length) selectConversation(list.items[0].id);
      await fetchHealth();
      setNotice('Connected. Your token is kept in this tab’s memory only.');
    } catch (err) { setError(err.message); }
    finally { setConnecting(false); }
  }

  function disconnect() {
    revision.current += 1;
    setToken(''); setTokenInput(''); setConversations([]); setConversation(null); setProducts([]);
    selectedRef.current = null; setSelectedId(null); setNotice('Disconnected.'); setError('');
    setLoadingChat(false); setOwnerText(''); ownerRequest.current = null; demoRequest.current = null;
  }

  async function mutation(action, operation) {
    if (busyRef.current) return;
    revision.current += 1;
    busyRef.current = true; setBusy(action); setError(''); setNotice('');
    try { await operation(); }
    catch (err) { setError(err.message); }
    finally { busyRef.current = false; setBusy(''); }
  }

  function applyConversation(next) {
    if (!next?.id) throw new Error('The backend returned an invalid conversation.');
    selectedRef.current = next.id; setSelectedId(next.id); setConversation(next);
    const message = next.messages?.at(-1);
    const summary = { ...next, messages: undefined, last_message: message?.text || next.last_message || '' };
    setConversations((items) => [summary, ...items.filter((item) => item.id !== next.id)]);
  }

  function sendCustomer(event) {
    event.preventDefault();
    const text = customerText.trim();
    const customer = customerId.trim();
    if (!token || !text || !customer) return;
    mutation('customer', async () => {
      const previous = demoRequest.current;
      if (!previous || previous.text !== text || previous.customer_id !== customer) {
        demoRequest.current = { customer_id: customer, text, external_id: `demo-${crypto.randomUUID()}` };
      }
      const result = await request('/demo/messages', { token, method: 'POST', body: demoRequest.current });
      applyConversation(result.conversation);
      demoRequest.current = null; setCustomerText('');
      setNotice('Customer message processed. This simulator never sends a live WhatsApp message.');
    });
  }

  function changeMode() {
    if (!conversation) return;
    const mode = conversation.mode === 'human' ? 'bot' : 'human';
    mutation('handover', async () => {
      const result = await request(`/conversations/${encodeURIComponent(conversation.id)}/handover`, { token, method: 'POST', body: { mode } });
      applyConversation(result.conversation);
      setNotice(mode === 'human' ? 'Owner takeover enabled. Automatic replies are paused.' : 'Agent resumed for new customer messages.');
    });
  }

  function sendOwner(event) {
    event.preventDefault();
    const text = ownerText.trim();
    if (!text || !conversation || conversation.mode !== 'human') return;
    mutation('owner', async () => {
      if (!ownerRequest.current || ownerRequest.current.conversationId !== conversation.id || ownerRequest.current.body.text !== text) {
        ownerRequest.current = { conversationId: conversation.id, body: { text, external_id: `owner-${crypto.randomUUID()}` } };
      }
      const result = await request(`/conversations/${encodeURIComponent(conversation.id)}/messages`, { token, method: 'POST', body: ownerRequest.current.body });
      applyConversation(result.conversation); setOwnerText(''); ownerRequest.current = null;
      setNotice('Owner reply saved. Check its delivery status below.');
    });
  }

  const filtered = conversations.filter((item) => `${item.customer_name || ''} ${item.customer_id || ''} ${lastText(item)}`.toLowerCase().includes(search.toLowerCase()));
  const humanCount = conversations.filter((item) => item.mode === 'human').length;
  const demoEnabled = Boolean(health?.demo_mode);
  const liveChannel = health?.channel === 'whatsapp';

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#main" aria-label="ShopSync AI home"><span className="brand-mark"><Icon name="bolt" size={23} /></span><span>ShopSync<span className="brand-ai">AI</span></span></a>
      <div className="sidebar-label">WORKSPACE</div>
      <div className="nav-item active"><Icon name="chat" /><span>Conversations</span><span className="nav-count">{conversations.length}</span></div>
      <div className="sidebar-note"><span className="small-label">YOUR MODULE</span><strong>UC-3</strong><span>Chat with Business</span><p>Customer & Commerce Agent</p></div>
      <div className="sidebar-footer"><span className="small-dot" />English · اردو · Roman Urdu</div>
    </aside>

    <main id="main" className="workspace">
      <header className="page-header"><div><div className="eyebrow">CUSTOMER & COMMERCE</div><h1>Conversations</h1><p>Helpful answers. A human whenever it matters.</p></div><div className={`environment-pill ${healthError ? 'offline' : ''}`}><span />{healthError ? 'Backend unavailable' : !health ? 'Checking backend…' : liveChannel ? 'WhatsApp channel' : 'Demo channel'}</div></header>

      <section className="connection-bar" aria-label="Backend connection">
        <div className="connection-icon"><Icon name="lock" /></div><div className="connection-copy"><strong>{token ? 'Workspace connected' : 'Connect your workspace'}</strong><span>{token ? `${API_BASE} · ${health?.storage || 'storage unknown'} · ${health?.llm || 'agent unknown'}` : 'Use the dashboard token from your backend configuration.'}</span></div>
        {token ? <div className="connection-actions"><button className="button subtle" type="button" onClick={() => refresh()} disabled={Boolean(busy)}><Icon name="refresh" size={16} />Refresh</button><button className="button subtle" type="button" onClick={disconnect} disabled={Boolean(busy)}>Disconnect</button></div> : <form onSubmit={connect} className="token-form"><label className="sr-only" htmlFor="token">Dashboard API token</label><input id="token" name="token" type="password" autoComplete="off" value={tokenInput} onChange={(event) => setTokenInput(event.target.value)} placeholder="Dashboard API token" required /><button className="button primary" disabled={connecting}>{connecting ? 'Connecting…' : 'Connect'}<Icon name="arrow" size={16} /></button></form>}
      </section>

      {(error || healthError) && <div role="alert" className="alert error"><strong>Connection or request issue</strong><span>{error || healthError}</span><button className="text-button" type="button" onClick={() => { fetchHealth(); refresh(); }}>Try refresh</button></div>}
      {notice && <div className="notice" role="status"><Icon name="check" size={16} />{notice}</div>}

      <section className="conversation-grid">
        <aside className="inbox panel" aria-label="Conversation list">
          <div className="panel-heading"><h2>Inbox <span>{conversations.length}</span></h2><span className="needs-owner">{humanCount} need owner</span></div>
          <div className="search-wrap"><label className="sr-only" htmlFor="search">Search conversations</label><input id="search" placeholder="Search customers or messages" value={search} onChange={(event) => setSearch(event.target.value)} /></div>
          <div className="inbox-list">
            {!token ? <div className="small-empty"><Icon name="lock" size={28} /><p>Connect to load your customer conversations.</p></div> : filtered.length === 0 ? <div className="small-empty"><Icon name="chat" size={28} /><p>{search ? 'No matching conversations.' : 'Your inbox is ready.'}</p>{!search && <span>Send a simulator message to start.</span>}</div> : filtered.map((item) => <button type="button" className={`conversation-row ${selectedId === item.id ? 'selected' : ''}`} key={item.id} onClick={() => selectConversation(item.id)} disabled={Boolean(busy)}><span className="avatar">{initials(item.customer_name || item.customer_id)}</span><span className="row-copy"><span className="row-top"><strong>{item.customer_name || item.customer_id}</strong><time dateTime={item.updated_at}>{formatTime(item.updated_at)}</time></span><span className="preview" dir="auto">{lastText(item)}</span><ModeBadge mode={item.mode} /></span></button>)}
          </div>
          <div className="inbox-footer">Refreshes every 5 seconds while connected</div>
        </aside>

        <section className="chat-panel panel" aria-label="Selected conversation">
          {loadingChat && !conversation ? <div className="chat-empty"><div className="empty-icon"><Icon name="refresh" size={30} /></div><h2>Loading conversation…</h2></div> : !conversation ? <div className="chat-empty"><div className="empty-icon"><Icon name="chat" size={32} /></div><span className="eyebrow">ONE CONVERSATION, FULL CONTEXT</span><h2>Make every reply count.</h2><p>Select a customer conversation, or try a message in the simulator.</p><div className="empty-features"><span><Icon name="bot" size={16} />Grounded agent replies</span><span><Icon name="user" size={16} />Owner takeover</span></div></div> : <>
            <div className="chat-heading"><span className="avatar large">{initials(conversation.customer_name || conversation.customer_id)}</span><div className="chat-title"><h2>{conversation.customer_name || conversation.customer_id}</h2><span>{conversation.customer_id} <span className="separator">·</span> {conversation.language === 'ur' ? 'Urdu' : conversation.language === 'roman_ur' ? 'Roman Urdu' : 'English'}</span></div><ModeBadge mode={conversation.mode} /></div>
            <div className={`handover-strip ${conversation.mode === 'human' ? 'human' : ''}`}><Icon name={conversation.mode === 'human' ? 'user' : 'bot'} size={18} /><div><strong>{conversation.mode === 'human' ? 'You’re in control' : 'Customer Agent is replying'}</strong><span>{conversation.mode === 'human' ? (conversation.handover_reason || 'Automatic replies are paused for this conversation.') : 'Answers use available business data.'}</span></div><button type="button" className="button small" onClick={changeMode} disabled={Boolean(busy)}>{busy === 'handover' ? 'Updating…' : conversation.mode === 'human' ? 'Resume agent' : 'Take over'}</button></div>
            <div className="messages" aria-live="polite" aria-relevant="additions text">{(conversation.messages || []).map((message) => <div key={message.id} className={`message-row ${message.role === 'customer' ? 'incoming' : message.role === 'system' ? 'system' : 'outgoing'}`}><div className="message"><div className="message-author">{message.role === 'customer' ? 'Customer' : message.role === 'owner' ? 'Business owner' : message.role === 'system' ? 'System' : 'Customer Agent'}</div><div className="message-text" dir="auto">{message.text}</div><div className="message-meta"><time dateTime={message.created_at}>{formatTime(message.created_at, true)}</time><span className={`delivery ${message.delivery_status || ''}`}>{message.delivery_status || 'saved'}</span></div></div></div>)}<div ref={scrollEnd} /></div>
            <form className="owner-composer" onSubmit={sendOwner}><label htmlFor="owner-reply">{conversation.mode === 'human' ? 'Reply as business owner' : 'Owner reply'}</label><div className="composer-row"><textarea id="owner-reply" dir="auto" rows="2" maxLength="2000" placeholder={conversation.mode === 'human' ? 'Write a helpful reply…' : 'Take over this conversation to reply.'} value={ownerText} onChange={(event) => setOwnerText(event.target.value)} disabled={conversation.mode !== 'human' || Boolean(busy)} /><button className="button primary icon-button" aria-label="Send owner reply" disabled={conversation.mode !== 'human' || !ownerText.trim() || Boolean(busy)}><Icon name="send" size={18} /></button></div><span className="composer-hint">{liveChannel ? 'Owner replies use the configured WhatsApp channel.' : 'Demo replies are saved here; no live WhatsApp message is sent.'}</span></form>
          </>}
        </section>

        <aside className="tools-column">
          <section className="simulator panel" aria-label="Customer simulator"><div className="panel-heading"><h2><Icon name="chat" size={18} />Customer simulator</h2><span className="demo-badge">TEST</span></div><p className="section-description">Try the customer side of a conversation without sending a WhatsApp message.</p>{health && !demoEnabled && <div className="inline-warning">The simulator is disabled by this backend. Use the connected WhatsApp channel.</div>}<form onSubmit={sendCustomer}><label htmlFor="customer-id">Customer ID</label><input id="customer-id" value={customerId} maxLength="80" onChange={(event) => setCustomerId(event.target.value)} disabled={!token || !demoEnabled || Boolean(busy)} required /><div className="field-heading"><label htmlFor="customer-message">Customer message</label><span>3 languages</span></div><div className="example-buttons">{EXAMPLES.map((example) => <button type="button" key={example.label} onClick={() => setCustomerText(example.text)} disabled={!token || !demoEnabled || Boolean(busy)}>{example.label}</button>)}</div><textarea id="customer-message" dir="auto" rows="4" maxLength="2000" value={customerText} onChange={(event) => setCustomerText(event.target.value)} placeholder="Ask about products, availability, or speak to the owner…" disabled={!token || !demoEnabled || Boolean(busy)} required /><button className="button primary full-width" disabled={!token || !demoEnabled || !customerId.trim() || !customerText.trim() || Boolean(busy)}>{busy === 'customer' ? 'Processing message…' : 'Send test message'}<Icon name="send" size={16} /></button><p className="form-hint">Try “I want to talk to a human” to test handover.</p></form></section>

          <section className="catalogue panel" aria-label="Read-only product catalogue"><div className="panel-heading"><h2><Icon name="package" size={18} />Business data</h2><span className="read-only">READ ONLY</span></div><p className="section-description">The agent uses these stored product facts.</p>{!token ? <p className="catalogue-empty">Connect to view the product catalogue.</p> : !products.length ? <p className="catalogue-empty">No products found. Seed your demo catalogue or connect your product data.</p> : <div className="product-list">{products.map((product) => <div className="product-row" key={product.id}><div className="product-symbol"><Icon name="package" size={18} /></div><div><strong>{product.name}</strong><span>{productPrice(product)}<span className="separator">·</span>{productStock(product)}</span></div></div>)}</div>}<div className="data-note"><Icon name="lock" size={14} /><span>Product and stock changes belong to their respective modules.</span></div></section>
        </aside>
      </section>
      <footer className="workspace-footer"><span>ShopSync AI <span className="separator">/</span> UC-3 implementation</span><span>{health?.llm === 'demo' ? 'Simulated replies · No live LLM connected' : health?.llm ? `Agent provider: ${health.llm}` : 'Backend connection required'}</span></footer>
    </main>
  </div>;
}

createRoot(document.getElementById('root')).render(<React.StrictMode><App /></React.StrictMode>);
