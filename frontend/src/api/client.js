const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');

async function request(path, token, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...options.headers,
      },
    });
  } catch {
    throw new Error('Cannot reach the backend. Check that the FastAPI server is running.');
  }

  const raw = await response.text();
  let data = {};
  if (raw) {
    try {
      data = JSON.parse(raw);
    } catch {
      throw new Error('The backend returned a response that is not valid JSON.');
    }
  }

  if (!response.ok) {
    const detail = data.detail || data.error || `Request failed (${response.status}).`;
    const message = Array.isArray(detail)
      ? detail.map((item) => item.msg || String(item)).join('; ')
      : typeof detail === 'string' ? detail : JSON.stringify(detail);
    throw new Error(message);
  }
  return data;
}

export function registerOwner({ name, email, businessName, password }) {
  return request('/api/account/signup', null, {
    method: 'POST',
    body: JSON.stringify({ name, email, business_name: businessName, password }),
  });
}

export function loginOwner({ email, password }) {
  return request('/api/account/login', null, {
    method: 'POST', body: JSON.stringify({ email, password }),
  });
}

export function requestPasswordReset({ email }) {
  return request('/api/account/password-reset/request', null, {
    method: 'POST', body: JSON.stringify({ email }),
  });
}

export function confirmPasswordReset({ email, code, password }) {
  return request('/api/account/password-reset/confirm', null, {
    method: 'POST', body: JSON.stringify({ email, code, password }),
  });
}

export function logoutOwner(token) {
  return request('/api/account/logout', token, { method: 'POST' });
}

export function getOwnerSettings(token) {
  return request('/api/account/settings', token);
}

export function updateOwnerSettings(token, changes) {
  return request('/api/account/settings', token, {
    method: 'PATCH', body: JSON.stringify(changes),
  });
}

export function getWhatsAppStatus(token) {
  return request('/api/integrations/whatsapp', token);
}

export function connectWhatsApp(token, details) {
  return request('/api/integrations/whatsapp/connect', token, {
    method: 'POST', body: JSON.stringify(details),
  });
}

export function verifyWhatsApp(token) {
  return request('/api/integrations/whatsapp/verify', token, { method: 'POST' });
}

export function disconnectWhatsApp(token) {
  return request('/api/integrations/whatsapp', token, { method: 'DELETE' });
}

export function sendOwnerRequest({ token, message, conversationId }) {
  return request('/api/owner/chat', token, {
    method: 'POST',
    body: JSON.stringify({ message, ...(conversationId ? { conversation_id: conversationId } : {}) }),
  });
}

export function getOwnerHistory({ token, conversationId }) {
  return request(`/api/owner/chat/history?conversation_id=${encodeURIComponent(conversationId)}`, token);
}

export function getAgentEvents(token) {
  return request('/api/owner/events', token);
}

// Customer inbox (UC-3). All owner endpoints reuse the UC-1 session token.
export function getConversations(token) {
  return request('/api/conversations', token);
}

export function getConversation(token, conversationId) {
  return request(`/api/conversations/${encodeURIComponent(conversationId)}`, token);
}

export function handoverConversation(token, conversationId, mode) {
  return request(`/api/conversations/${encodeURIComponent(conversationId)}/handover`, token, {
    method: 'POST', body: JSON.stringify({ mode }),
  });
}

export function sendConversationReply(token, conversationId, body) {
  return request(`/api/conversations/${encodeURIComponent(conversationId)}/messages`, token, {
    method: 'POST', body: JSON.stringify(body),
  });
}

export function simulateCustomerMessage(token, body) {
  return request('/api/demo/messages', token, { method: 'POST', body: JSON.stringify(body) });
}

// UC-4 preserves the validated standalone orders API, but uses UC-1 sessions.
export function getOrderProducts(token) {
  return request('/api/products', token);
}

export function addOrderProduct(token, body) {
  return request('/api/products', token, { method: 'POST', body: JSON.stringify(body) });
}

export function addProductStock(token, productId, quantity) {
  return request(`/api/products/${encodeURIComponent(productId)}/restock`, token, {
    method: 'POST', body: JSON.stringify({ quantity }),
  });
}

export function getOrders(token) {
  return request('/api/orders', token);
}

export function createOrderDraft(token, body) {
  return request('/api/orders/drafts', token, { method: 'POST', body: JSON.stringify(body) });
}

export function editOrderDraft(token, orderId, body) {
  return request(`/api/orders/${encodeURIComponent(orderId)}/draft`, token, { method: 'PATCH', body: JSON.stringify(body) });
}

export function sendOrderAction(token, path, body = {}) {
  return request(path, token, { method: 'POST', body: JSON.stringify(body) });
}
