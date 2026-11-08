import { useState } from 'react';
import { logoutOwner } from '../api/client.js';

export function useOwnerSession() {
  // Keep the bearer token in memory. A page reload asks the owner to log in
  // again; it is never written to localStorage or bundled into the frontend.
  const [session, setSession] = useState(null);

  async function signOut() {
    if (!session) return;
    try {
      await logoutOwner(session.access_token);
    } catch {
      // An expired session or unavailable backend must not trap the owner in
      // the signed-in screen. The server token still expires after 12 hours.
    } finally {
      setSession(null);
    }
  }

  function updateOwnerProfile(profile) {
    setSession((current) => current ? {
      ...current,
      owner: { ...current.owner, business_name: profile.name },
    } : current);
  }

  return { session, authenticate: setSession, signOut, updateOwnerProfile };
}
