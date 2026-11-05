const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');

async function request(path, token, options = {}) {
  if (!token) throw new Error('Enter the local demo owner token to continue.');

  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers: {
        Authorization: `Bearer ${token}`,
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

export function sendOwnerRequest({ token, message, conversationId }) {
  return request('/api/owner/chat', token, {
    method: 'POST',
    body: JSON.stringify({ message, ...(conversationId ? { conversation_id: conversationId } : {}) }),
  });
}

export function getOwnerHistory({ token, conversationId }) {
  return request(`/api/owner/chat/history?conversation_id=${encodeURIComponent(conversationId)}`, token);
}
