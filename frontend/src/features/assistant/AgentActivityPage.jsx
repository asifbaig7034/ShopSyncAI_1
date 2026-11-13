import { useEffect, useState } from 'react';
import { getAgentEvents } from '../../api/client.js';

const EVENT_NAMES = {
  'order.confirmed': 'Order confirmed',
  'order.cancelled': 'Order cancelled',
  'stock.low': 'Low stock',
  'stock.replenished': 'Stock replenished',
  'risk.held': 'Order held for owner review',
};

function eventSummary(event) {
  const { order_id: orderId, product_id: productId, stock_quantity: quantity } = event.details || {};
  if (event.event_type.startsWith('stock.')) return `Product ${productId || 'unknown'}: ${quantity ?? '?'} remaining`;
  return `Order ${orderId || 'unknown'}`;
}

export default function AgentActivityPage({ token }) {
  const [activity, setActivity] = useState({ events: [], pending: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    async function refresh() {
      try {
        const data = await getAgentEvents(token);
        if (active) { setActivity(data); setError(''); }
      } catch (err) {
        if (active) setError(err.message);
      } finally {
        if (active) setLoading(false);
      }
    }
    refresh();
    const interval = window.setInterval(refresh, 5000);
    return () => { active = false; window.clearInterval(interval); };
  }, [token]);

  return (
    <div className="agent-activity" aria-live="polite">
      <p>Committed orders and stock changes reach enabled agents automatically. This view refreshes every five seconds.</p>
      {error && <div className="notice" role="alert">{error}</div>}
      {activity.pending > 0 && <p role="status">{activity.pending} event(s) queued for retry. Orders remain saved.</p>}
      {loading ? <div className="loading">Loading activity…</div> : activity.events.length === 0 ? (
        <p className="feature-empty">No real order or stock events yet. Confirm an order to see the agent flow.</p>
      ) : (
        <ul className="agent-activity__list">
          {activity.events.map((event) => (
            <li key={event.event_id} className="agent-activity__item">
              <div className="agent-activity__top">
                <strong>{EVENT_NAMES[event.event_type] || event.event_type}</strong>
                <span>{event.status.replaceAll('_', ' ')}</span>
              </div>
              <p>{eventSummary(event)}</p>
              <small>Agents: {event.agent_responses.length > 0
                ? event.agent_responses.map((response) => response.agent).join(', ')
                : event.skipped_agents?.length > 0 ? 'Skipped because disabled'
                : event.target_agents.length > 0 ? 'Awaiting delivery' : 'No active subscriber yet'}
              </small>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
