import { useEffect, useRef, useState } from 'react';
import { addOrderProduct, addProductStock, createOrderDraft, editOrderDraft, getOrderProducts, getOrders, sendOrderAction } from '../../api/client.js';

const makeId = () => globalThis.crypto?.randomUUID?.() || `local-${Date.now()}-${Math.random()}`;
const asMoney = (cents) => `PKR ${(Number(cents || 0) / 100).toLocaleString('en-PK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export default function OrdersPage({ token }) {
  const [products, setProducts] = useState([]);
  const [orders, setOrders] = useState([]);
  const [productForm, setProductForm] = useState({ sku: '', name: '', price: '', stock: '' });
  const [draftForm, setDraftForm] = useState({ phone: '', name: '', address: '', payment: 'COD', productId: '', quantity: 1 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const sourceKey = useRef('');

  async function refresh() {
    const [catalogue, result] = await Promise.all([getOrderProducts(token), getOrders(token)]);
    setProducts(Array.isArray(catalogue) ? catalogue : catalogue.items || []);
    setOrders(Array.isArray(result) ? result : result.items || []);
  }

  useEffect(() => {
    let active = true;
    Promise.all([getOrderProducts(token), getOrders(token)]).then(([catalogue, result]) => {
      if (!active) return;
      setProducts(Array.isArray(catalogue) ? catalogue : catalogue.items || []);
      setOrders(Array.isArray(result) ? result : result.items || []);
    }).catch((cause) => { if (active) setError(cause.message); });
    return () => { active = false; };
  }, [token]);

  async function perform(action, success = 'Action saved.') {
    if (busy) return;
    setBusy(true);
    setError('');
    setNotice('');
    try { await action(); await refresh(); setNotice(success); }
    catch (cause) { setError(cause.message); }
    finally { setBusy(false); }
  }

  function submitProduct(event) {
    event.preventDefault();
    const cents = Math.round(Number(productForm.price) * 100);
    if (!Number.isSafeInteger(cents) || cents < 0 || !Number.isInteger(Number(productForm.stock)) || Number(productForm.stock) < 0) { setError('Enter a valid price and stock quantity.'); return; }
    perform(async () => {
      await addOrderProduct(token, { sku: productForm.sku.trim(), name: productForm.name.trim(), price_cents: cents, stock_qty: Number(productForm.stock) });
      setProductForm({ sku: '', name: '', price: '', stock: '' });
    }, 'Product added.');
  }

  function submitDraft(event) {
    event.preventDefault();
    if (!draftForm.productId || !Number.isInteger(Number(draftForm.quantity)) || Number(draftForm.quantity) < 1) { setError('Select a product and a valid quantity.'); return; }
    if (!sourceKey.current) sourceKey.current = makeId();
    perform(async () => {
      await createOrderDraft(token, { phone: draftForm.phone.trim(), name: draftForm.name.trim(), delivery_address: draftForm.address.trim(), payment_method: draftForm.payment, items: [{ product_id: draftForm.productId, quantity: Number(draftForm.quantity) }], source_key: sourceKey.current });
      sourceKey.current = '';
      setDraftForm({ phone: '', name: '', address: '', payment: 'COD', productId: '', quantity: 1 });
    }, 'Draft created. Review the summary and confirm when the customer agrees.');
  }

  function orderAction(action, order) {
    if (busy) return;
    if (action === 'edit') {
      const sku = window.prompt('Product SKU:', order.items?.[0]?.sku || '');
      if (sku === null) return;
      const product = products.find((item) => item.sku?.toUpperCase() === sku.trim().toUpperCase());
      if (!product) { setError('Product SKU was not found.'); return; }
      const quantity = window.prompt('Quantity:', String(order.items?.[0]?.quantity || 1));
      if (quantity === null) return;
      const address = window.prompt('Delivery address:', order.delivery_address || '');
      if (address === null) return;
      const payment = window.prompt('Payment method: COD or BANK_TRANSFER', order.payment_method || 'COD');
      if (payment === null) return;
      if (!Number.isInteger(Number(quantity)) || Number(quantity) < 1 || Number(quantity) > 100 || !address.trim() || !['COD', 'BANK_TRANSFER'].includes(payment.trim().toUpperCase())) {
        setError('Provide a quantity from 1 to 100, delivery address, and valid payment method.');
        return;
      }
      perform(() => editOrderDraft(token, order.id, { items: [{ product_id: product.id, quantity: Number(quantity) }], delivery_address: address.trim(), payment_method: payment.trim().toUpperCase() }), 'Draft updated. Review it with the customer before confirming.');
      return;
    }
    let route = `/api/orders/${encodeURIComponent(order.id)}/${action}`;
    let payload = {};
    if (action === 'cancel' && !window.confirm('Cancel this unshipped order and restore its stock?')) return;
    if (action === 'confirm' && !window.confirm('Customer reviewed the order summary and confirmed this order?')) return;
    if (action === 'ship' && !window.confirm('Mark this order as shipped?')) return;
    if (action === 'approve-risk' || action === 'reject-risk') {
      route = `/api/orders/${encodeURIComponent(order.id)}/risk-decision`;
      payload = { approve: action === 'approve-risk', reason: window.prompt('Decision note (optional):', '') };
      if (payload.reason === null) return;
    }
    if (action === 'return' || action === 'refund') {
      const reason = window.prompt('Customer reason for the request:', '');
      if (!reason?.trim()) return;
      route = `/api/orders/${encodeURIComponent(order.id)}/returns`;
      payload = { kind: action.toUpperCase(), reason: reason.trim() };
    }
    if (action === 'approve-return' || action === 'reject-return') {
      const reason = window.prompt(action === 'reject-return' ? 'Reason for rejection (required):' : 'Decision note (optional):', '');
      if (reason === null || (action === 'reject-return' && !reason.trim())) return;
      route = `/api/returns/${encodeURIComponent(order.return_request.id)}/decision`;
      payload = { approve: action === 'approve-return', reason: reason.trim() };
    }
    if (action === 'accept-item' || action === 'reject-item') {
      const reason = action === 'reject-item' ? window.prompt('Reason for rejecting the returned item (required):', '') : '';
      if (reason === null || (action === 'reject-item' && !reason.trim())) return;
      route = `/api/returns/${encodeURIComponent(order.return_request.id)}/inspect`;
      payload = { accept: action === 'accept-item', reason: reason.trim() };
    }
    perform(() => sendOrderAction(token, route, payload));
  }

  function actionsFor(order) {
    const buttons = [];
    const add = (key, label) => buttons.push(<button className="button button--quiet" type="button" key={key} disabled={busy} onClick={() => orderAction(key, order)}>{label}</button>);
    if (order.status === 'DRAFT') { add('edit', 'Edit draft'); add('confirm', 'Confirm'); }
    if (order.status === 'RISK_HOLD') { add('approve-risk', 'Approve risk'); add('reject-risk', 'Reject risk'); }
    if (['DRAFT', 'RISK_HOLD', 'CONFIRMED'].includes(order.status)) add('cancel', 'Cancel');
    if (order.status === 'CONFIRMED') add('ship', 'Mark shipped');
    if (['CONFIRMED', 'SHIPPED'].includes(order.status) && !order.return_request) { add('return', 'Request return'); add('refund', 'Request refund'); }
    if (order.return_request?.status === 'PENDING') { add('approve-return', 'Approve request'); add('reject-return', 'Reject request'); }
    if (order.return_request?.status === 'APPROVED_AWAITING_RECEIPT') { add('accept-item', 'Item accepted'); add('reject-item', 'Item rejected'); }
    return buttons;
  }

  return <div className="feature-page" aria-label="Owner orders workspace">
    {error && <div className="feature-error" role="alert">{error}</div>}
    {notice && <div className="feature-success" role="status">{notice}</div>}
    <div className="feature-tools"><p>Create a customer draft, manage confirmed orders and review sensitive decisions.</p><button type="button" className="button button--quiet" disabled={busy} onClick={() => perform(() => refresh(), 'Orders updated.')}>Refresh</button></div>
    <div className="orders-forms">
      <form className="feature-form" onSubmit={submitProduct}><h3>Add demo product</h3><label>SKU<input value={productForm.sku} onChange={(event) => setProductForm({ ...productForm, sku: event.target.value })} required maxLength={32} /></label><label>Name<input value={productForm.name} onChange={(event) => setProductForm({ ...productForm, name: event.target.value })} required maxLength={100} /></label><div className="feature-row"><label>Price (PKR)<input type="number" min="0" step="0.01" value={productForm.price} onChange={(event) => setProductForm({ ...productForm, price: event.target.value })} required /></label><label>Stock<input type="number" min="0" step="1" value={productForm.stock} onChange={(event) => setProductForm({ ...productForm, stock: event.target.value })} required /></label></div><button className="button button--primary" disabled={busy}>Add product</button></form>
      <form className="feature-form" onSubmit={submitDraft} onChange={() => { sourceKey.current = ''; }}><h3>Create order draft</h3><label>Customer phone<input type="tel" value={draftForm.phone} onChange={(event) => setDraftForm({ ...draftForm, phone: event.target.value })} required /></label><label>Customer name<input value={draftForm.name} onChange={(event) => setDraftForm({ ...draftForm, name: event.target.value })} maxLength={100} /></label><label>Product<select value={draftForm.productId} onChange={(event) => setDraftForm({ ...draftForm, productId: event.target.value })} required><option value="">Select product</option>{products.filter((product) => product.active !== false).map((product) => <option key={product.id} value={product.id}>{product.sku || product.name} · {asMoney(product.price_cents ?? Math.round(Number(product.price || 0) * 100))} · {product.stock_qty ?? product.stock_quantity ?? product.stock} available</option>)}</select></label><div className="feature-row"><label>Quantity<input type="number" min="1" max="100" value={draftForm.quantity} onChange={(event) => setDraftForm({ ...draftForm, quantity: event.target.value })} required /></label><label>Payment<select value={draftForm.payment} onChange={(event) => setDraftForm({ ...draftForm, payment: event.target.value })}><option value="COD">Cash on delivery</option><option value="BANK_TRANSFER">Bank transfer</option></select></label></div><label>Delivery address<input value={draftForm.address} onChange={(event) => setDraftForm({ ...draftForm, address: event.target.value })} required /></label><button className="button button--primary" disabled={busy || !products.length}>Create draft</button></form>
    </div>
    <section className="feature-section"><h3>Products</h3>{products.length === 0 ? <p className="feature-empty">No products yet.</p> : <div className="feature-list">{products.map((product) => <div className="feature-list-item" key={product.id}><strong>{product.name} · {product.sku || 'No SKU'}</strong><span>{asMoney(product.price_cents ?? Math.round(Number(product.price || 0) * 100))} · {product.stock_qty ?? product.stock_quantity ?? product.stock} available</span><button type="button" className="button button--quiet" disabled={busy} onClick={() => { const quantity = window.prompt('Number of units to add:', '1'); if (quantity === null) return; if (!Number.isInteger(Number(quantity)) || Number(quantity) < 1) { setError('Enter a whole-number quantity greater than zero.'); return; } perform(() => addProductStock(token, product.id, Number(quantity)), 'Stock updated.'); }}>Restock</button></div>)}</div>}</section>
    <section className="feature-section"><h3>Orders</h3>{orders.length === 0 ? <p className="feature-empty">No orders yet. Add a product and create a draft.</p> : <div className="feature-list">{orders.map((order) => <article className="feature-list-item" key={order.id}><div className="feature-item-header"><strong>{order.id}</strong><span className="feature-status">{order.status?.replaceAll('_', ' ')}</span></div><span>{order.customer_name || order.customer_phone} · {asMoney(order.total_cents)} · {order.payment_method}</span><span>{(order.items || []).map((item) => `${item.quantity} × ${item.name}`).join(', ')} · {order.delivery_address}</span>{order.risk_score != null && <small>Risk: {order.risk_score}/100 · {(order.risk_reasons || []).join('; ')}</small>}{order.return_request && <small>{order.return_request.kind} · {order.return_request.status} · {order.return_request.reason}</small>}<div className="feature-actions">{actionsFor(order)}</div></article>)}</div>}</section>
  </div>;
}
