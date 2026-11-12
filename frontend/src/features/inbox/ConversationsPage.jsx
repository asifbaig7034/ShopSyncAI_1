import { useEffect, useRef, useState } from 'react';
import { getConversations, getConversation, handoverConversation, sendConversationReply, simulateCustomerMessage } from '../../api/client.js';

const id = () => (globalThis.crypto?.randomUUID?.() || `local-${Date.now()}-${Math.random()}`);
const asTime = (value) => value ? new Date(value).toLocaleString() : '';

export default function ConversationsPage({ token }) {
  const [items, setItems] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [conversation, setConversation] = useState(null);
  const [reply, setReply] = useState('');
  const [customerId, setCustomerId] = useState('923001234567');
  const [testMessage, setTestMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pendingReply = useRef('');
  const pendingTestMessage = useRef('');

  async function refresh(preferredId = selectedId) {
    const result = await getConversations(token);
    const conversations = result.items || [];
    setItems(conversations);
    const nextId = preferredId || conversations[0]?.id || '';
    if (nextId !== selectedId) {
      setReply('');
      pendingReply.current = '';
    }
    if (nextId) {
      const detail = await getConversation(token, nextId);
      setConversation(detail.conversation);
    } else {
      setConversation(null);
    }
    setSelectedId(nextId);
  }

  useEffect(() => {
    let active = true;
    getConversations(token).then((result) => {
      if (!active) return;
      setItems(result.items || []);
      const firstId = result.items?.[0]?.id;
      if (!firstId) return;
      setSelectedId(firstId);
      return getConversation(token, firstId).then((detail) => { if (active) setConversation(detail.conversation); });
    }).catch((cause) => { if (active) setError(cause.message); });
    return () => { active = false; };
  }, [token]);

  async function select(nextId) {
    if (busy || nextId === selectedId) return;
    setError('');
    setReply('');
    pendingReply.current = '';
    setSelectedId(nextId);
    try { setConversation((await getConversation(token, nextId)).conversation); }
    catch (cause) { setError(cause.message); }
  }

  async function perform(action) {
    if (busy) return;
    setBusy(true);
    setError('');
    try { await action(); }
    catch (cause) { setError(cause.message); }
    finally { setBusy(false); }
  }

  function changeMode() {
    if (!conversation) return;
    perform(async () => {
      const mode = conversation.mode === 'human' ? 'bot' : 'human';
      const result = await handoverConversation(token, conversation.id, mode);
      setConversation(result.conversation);
      await refresh(conversation.id);
    });
  }

  function sendReply(event) {
    event.preventDefault();
    const text = reply.trim();
    if (!conversation || conversation.mode !== 'human' || !text) return;
    if (!pendingReply.current) pendingReply.current = id();
    perform(async () => {
      const result = await sendConversationReply(token, conversation.id, { text, external_id: pendingReply.current });
      pendingReply.current = '';
      setReply('');
      setConversation(result.conversation);
      await refresh(conversation.id);
    });
  }

  function sendTest(event) {
    event.preventDefault();
    const text = testMessage.trim();
    const customer = customerId.trim();
    if (!customer || !text) return;
    if (!pendingTestMessage.current) pendingTestMessage.current = id();
    perform(async () => {
      const result = await simulateCustomerMessage(token, { customer_id: customer, text, external_id: pendingTestMessage.current });
      pendingTestMessage.current = '';
      setTestMessage('');
      await refresh(result.conversation?.id);
    });
  }

  return <div className="feature-page" aria-label="Customer conversation inbox">
    {error && <div className="feature-error" role="alert">{error}</div>}
    <div className="feature-tools"><p>Customer conversations and owner handover. Test messages are only for the local demo.</p><button className="button button--quiet" type="button" disabled={busy} onClick={() => perform(() => refresh())}>Refresh</button></div>
    <div className="inbox-grid">
      <div className="inbox-list" aria-label="Conversations">
        {items.length === 0 && <p className="feature-empty">No customer conversations yet.</p>}
        {items.map((item) => <button type="button" key={item.id} onClick={() => select(item.id)} className={`inbox-item ${selectedId === item.id ? 'inbox-item--active' : ''}`}><strong>{item.customer_name || item.customer_id}</strong><small>{item.mode === 'human' ? 'Owner handover' : 'Agent active'} · {asTime(item.updated_at)}</small><span dir="auto">{item.last_message?.text || ''}</span></button>)}
      </div>
      <div className="inbox-thread">
        {conversation ? <>
          <div className="inbox-thread__header"><div><strong>{conversation.customer_name || conversation.customer_id}</strong><small>{conversation.customer_id} · {conversation.language || 'English'}</small></div><button className="button button--secondary" type="button" disabled={busy} onClick={changeMode}>{conversation.mode === 'human' ? 'Resume agent' : 'Take over'}</button></div>
          {conversation.handover_reason && conversation.mode === 'human' && <div className="feature-note">{conversation.handover_reason}</div>}
          <div className="inbox-messages" aria-live="polite">{(conversation.messages || []).map((message) => <div className={`inbox-message inbox-message--${message.role}`} key={message.id}><strong>{message.role === 'customer' ? 'Customer' : message.role === 'owner' ? 'Owner' : message.role === 'assistant' ? 'Agent' : 'System'}</strong><span dir="auto">{message.text}</span><small>{asTime(message.created_at)} · {message.delivery_status || 'saved'}</small></div>)}</div>
          <form className="feature-form" onSubmit={sendReply}><label htmlFor="inbox-owner-reply">Reply as owner</label><div className="feature-row"><input id="inbox-owner-reply" value={reply} maxLength={2000} onChange={(event) => { setReply(event.target.value); pendingReply.current = ''; }} disabled={busy || conversation.mode !== 'human'} placeholder={conversation.mode === 'human' ? 'Write a reply...' : 'Take over before replying'} /><button type="submit" className="button button--primary" disabled={busy || conversation.mode !== 'human' || !reply.trim()}>Save local reply</button></div><p>Replies are stored for this demo and are not sent to WhatsApp.</p></form>
        </> : <p className="feature-empty">Select a conversation to see its messages.</p>}
      </div>
    </div>
    <form className="feature-form feature-form--demo" onSubmit={sendTest}><h3>Local customer simulator</h3><p>This does not send a WhatsApp message.</p><div className="feature-row"><label htmlFor="inbox-test-customer">Customer phone (fictional)<input id="inbox-test-customer" value={customerId} onChange={(event) => { setCustomerId(event.target.value); pendingTestMessage.current = ''; }} pattern="[0-9]{8,15}" title="Enter 8 to 15 digits including country code" maxLength={15} required /></label><label htmlFor="inbox-test-text">Customer message<input id="inbox-test-text" dir="auto" value={testMessage} onChange={(event) => { setTestMessage(event.target.value); pendingTestMessage.current = ''; }} maxLength={2000} placeholder="Ask about a product or type ORDER SKU 1 | address | COD" required /></label><button className="button button--primary" type="submit" disabled={busy || !customerId.trim() || !testMessage.trim()}>Send test</button></div></form>
  </div>;
}
