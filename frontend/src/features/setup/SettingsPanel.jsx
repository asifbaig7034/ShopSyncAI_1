import { useEffect, useState } from 'react';
import {
  connectWhatsApp, disconnectWhatsApp, getOwnerSettings, getWhatsAppStatus,
  updateOwnerSettings, verifyWhatsApp,
} from '../../api/client.js';

const AGENTS = [
  { id: 'customer_commerce', name: 'Customer & Commerce', description: 'Answers questions about saved demonstration orders.' },
  { id: 'inventory_logistics', name: 'Inventory & Logistics', description: 'Answers questions about saved products and stock.' },
];

export default function SettingsPanel({ token, onProfileSaved, onSavingChange }) {
  const [profile, setProfile] = useState(null);
  const [agents, setAgents] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState('');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [reload, setReload] = useState(0);
  const [whatsApp, setWhatsApp] = useState(null);
  const [whatsAppLoading, setWhatsAppLoading] = useState(true);
  const [whatsAppBusy, setWhatsAppBusy] = useState(false);
  const [whatsAppError, setWhatsAppError] = useState('');
  const [whatsAppNotice, setWhatsAppNotice] = useState('');
  const [whatsAppReload, setWhatsAppReload] = useState(0);
  const [credentials, setCredentials] = useState({ waba_id: '', phone_number_id: '', access_token: '' });

  useEffect(() => { onSavingChange(!!saving || whatsAppBusy); }, [saving, whatsAppBusy, onSavingChange]);

  useEffect(() => {
    let active = true;
    getOwnerSettings(token)
      .then((settings) => {
        if (!active) return;
        setProfile(settings.business_profile);
        setAgents(settings.agent_settings);
      })
      .catch((err) => { if (active) setError(err.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [token, reload]);

  useEffect(() => {
    let active = true;
    getWhatsAppStatus(token)
      .then((status) => {
        if (!active) return;
        setWhatsApp(status);
        setCredentials({ waba_id: status.waba_id || '', phone_number_id: status.phone_number_id || '', access_token: '' });
      })
      .catch((err) => { if (active) setWhatsAppError(err.message); })
      .finally(() => { if (active) setWhatsAppLoading(false); });
    return () => { active = false; };
  }, [token, whatsAppReload]);

  async function saveProfile(event) {
    event.preventDefault();
    if (saving || whatsAppBusy || !profile) return;
    setError('');
    setSuccess('');
    if (profile.name.trim().length < 2) {
      setError('Enter a brand name of at least two characters.');
      return;
    }
    setSaving('profile');
    try {
      const settings = await updateOwnerSettings(token, {
        business_profile: { ...profile, name: profile.name.trim(), contact: profile.contact.trim(), currency: profile.currency.trim().toUpperCase() },
      });
      setProfile(settings.business_profile);
      onProfileSaved(settings.business_profile);
      setSuccess('Business profile saved.');
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving('');
    }
  }

  async function saveAgents(event) {
    event.preventDefault();
    if (saving || whatsAppBusy || !agents) return;
    setError('');
    setSuccess('');
    setSaving('agents');
    try {
      const settings = await updateOwnerSettings(token, { agent_settings: agents });
      setAgents(settings.agent_settings);
      setSuccess('Agent settings saved.');
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving('');
    }
  }

  async function connectChannel(event) {
    event.preventDefault();
    if (saving || whatsAppBusy) return;
    setWhatsAppBusy(true);
    setWhatsAppError('');
    setWhatsAppNotice('');
    try {
      const status = await connectWhatsApp(token, credentials);
      setWhatsApp(status);
      setCredentials({ waba_id: status.waba_id || '', phone_number_id: status.phone_number_id || '', access_token: '' });
      setWhatsAppNotice('Meta read access verified. Messaging and webhooks are separate UC-3 steps.');
    } catch (err) {
      setCredentials((previous) => ({ ...previous, access_token: '' }));
      setWhatsAppError(err.message);
    } finally {
      setWhatsAppBusy(false);
    }
  }

  async function recheckChannel() {
    if (saving || whatsAppBusy) return;
    setWhatsAppBusy(true);
    setWhatsAppError('');
    setWhatsAppNotice('');
    try {
      const status = await verifyWhatsApp(token);
      setWhatsApp(status);
      if (status.status === 'meta_read_verified') {
        setWhatsAppNotice('Meta read access verified again. This does not test messaging or webhooks.');
      } else {
        setWhatsAppError('Meta read access needs attention. Check the saved credentials and retry.');
      }
    } catch (err) {
      setWhatsAppError(err.message);
      setWhatsAppReload((value) => value + 1);
    } finally {
      setWhatsAppBusy(false);
    }
  }

  async function removeChannel() {
    if (saving || whatsAppBusy || !window.confirm('Remove the saved WhatsApp credentials from ShopSync AI? This does not disconnect the number at Meta.')) return;
    setWhatsAppBusy(true);
    setWhatsAppError('');
    setWhatsAppNotice('');
    try {
      const status = await disconnectWhatsApp(token);
      setWhatsApp(status);
      setCredentials({ waba_id: '', phone_number_id: '', access_token: '' });
      setWhatsAppNotice('Saved credentials removed from ShopSync AI. The Meta account was not changed.');
    } catch (err) {
      setWhatsAppError(err.message);
    } finally {
      setWhatsAppBusy(false);
    }
  }

  if (loading) return <div className="settings-panel"><p className="loading" role="status">Loading owner settings…</p></div>;
  if (!profile || !agents) return <div className="settings-panel"><p className="settings-message settings-message--error" role="alert">{error || 'Could not load settings.'}</p><button className="button button--secondary" type="button" onClick={() => { setError(''); setLoading(true); setReload((value) => value + 1); }}>Retry loading</button></div>;

  return (
    <div className="settings-panel">
      <p className="settings-lead">Manage your brand profile and the agents available for owner chat.</p>
      {error && <p className="settings-message settings-message--error" role="alert">{error}</p>}
      {success && <p className="settings-message settings-message--success" role="status">{success}</p>}

      <form className="settings-section settings-fields" onSubmit={saveProfile}>
        <h3>Business profile</h3>
        <label htmlFor="brand-name">Brand name</label>
        <input id="brand-name" required minLength="2" maxLength="120" value={profile.name} onChange={(event) => setProfile({ ...profile, name: event.target.value })} disabled={!!saving || whatsAppBusy} />
        <label htmlFor="business-contact">Contact (optional)</label>
        <input id="business-contact" maxLength="100" value={profile.contact} onChange={(event) => setProfile({ ...profile, contact: event.target.value })} placeholder="Business phone or email" disabled={!!saving || whatsAppBusy} />
        <div className="settings-field-row">
          <div><label htmlFor="business-currency">Currency code</label><input id="business-currency" required minLength="3" maxLength="3" pattern="[a-zA-Z]{3}" value={profile.currency} onChange={(event) => setProfile({ ...profile, currency: event.target.value })} disabled={!!saving || whatsAppBusy} /></div>
          <div><label htmlFor="business-language">Preferred language</label><select id="business-language" value={profile.language} onChange={(event) => setProfile({ ...profile, language: event.target.value })} disabled={!!saving || whatsAppBusy}><option>English</option><option>Urdu</option><option>Roman Urdu</option></select></div>
        </div>
        <p className="settings-help">Currency and language are saved as preferences. Existing demo prices remain in PKR and are not converted; customer language handling belongs to a later use case.</p>
        <button className="button button--primary" type="submit" disabled={!!saving || whatsAppBusy}>{saving === 'profile' ? 'Saving…' : 'Save profile'}</button>
      </form>

      <form className="settings-section" onSubmit={saveAgents}>
        <h3>Available agents</h3>
        <p className="settings-help">These switches control the two implemented read-only handlers. Other specialist agents will appear when they are implemented.</p>
        {AGENTS.map(({ id, name, description }) => (
          <label key={id} className="settings-agent">
            <span><strong>{name}</strong><small>{description}</small></span>
            <input type="checkbox" checked={!!agents[id]} onChange={(event) => setAgents({ ...agents, [id]: event.target.checked })} disabled={!!saving || whatsAppBusy} />
          </label>
        ))}
        {!agents.customer_commerce && !agents.inventory_logistics && <p className="settings-help settings-help--warning" role="note">With both agents off, owner chat cannot answer requests until you turn one back on.</p>}
        <button className="button button--primary" type="submit" disabled={!!saving || whatsAppBusy}>{saving === 'agents' ? 'Saving…' : 'Save agent settings'}</button>
      </form>

      <section className="settings-section settings-fields" aria-labelledby="whatsapp-settings-title">
        <h3 id="whatsapp-settings-title">WhatsApp Business API</h3>
        <p className="settings-help">Verify read access to a WhatsApp Business Account and its phone number. Message sending, webhook setup, Instagram, Facebook, courier and payment connections belong to later steps.</p>
        {whatsAppLoading ? <p className="loading" role="status">Loading WhatsApp status…</p> : <>
          {whatsApp && <p className="settings-channel-status" role="status"><strong>Status: </strong>{whatsApp.status === 'meta_read_verified' ? 'Meta read access last verified' : whatsApp.status === 'needs_attention' ? 'Verification needs attention' : 'Not connected'}{whatsApp.verified_name ? ` · ${whatsApp.verified_name}` : ''}{whatsApp.display_phone_number ? ` · ${whatsApp.display_phone_number}` : ''}{whatsApp.verified_at ? ` · Checked ${new Date(whatsApp.verified_at).toLocaleString()}` : ''}</p>}
          {whatsAppError && <p className="settings-message settings-message--error" role="alert">{whatsAppError}</p>}
          {whatsAppNotice && <p className="settings-message settings-message--success" role="status">{whatsAppNotice}</p>}
          {whatsApp ? <>
            <form className="settings-channel-form" onSubmit={connectChannel}>
              <label htmlFor="whatsapp-waba">WhatsApp Business Account ID</label>
              <input id="whatsapp-waba" required pattern="[0-9]{5,25}" maxLength="25" inputMode="numeric" autoComplete="off" value={credentials.waba_id} onChange={(event) => setCredentials({ ...credentials, waba_id: event.target.value })} disabled={!!saving || whatsAppBusy} />
              <label htmlFor="whatsapp-phone-id">Phone number ID</label>
              <input id="whatsapp-phone-id" required pattern="[0-9]{5,25}" maxLength="25" inputMode="numeric" autoComplete="off" value={credentials.phone_number_id} onChange={(event) => setCredentials({ ...credentials, phone_number_id: event.target.value })} disabled={!!saving || whatsAppBusy} />
              <label htmlFor="whatsapp-token">Meta access token</label>
              <input id="whatsapp-token" type="password" required minLength="20" maxLength="4096" autoComplete="off" value={credentials.access_token} onChange={(event) => setCredentials({ ...credentials, access_token: event.target.value })} disabled={!!saving || whatsAppBusy} placeholder="Enter a new token to verify or replace the connection" />
              <button className="button button--primary" type="submit" disabled={!!saving || whatsAppBusy}>{whatsAppBusy ? 'Checking…' : 'Verify and save'}</button>
            </form>
            {whatsApp.status !== 'not_connected' && <div className="settings-channel-actions">
              <button className="button button--secondary" type="button" onClick={recheckChannel} disabled={!!saving || whatsAppBusy || !!credentials.access_token}>Recheck saved token</button>
              <button className="button button--quiet" type="button" onClick={removeChannel} disabled={!!saving || whatsAppBusy}>Remove saved connection</button>
            </div>}
          </> : <button className="button button--secondary" type="button" onClick={() => { setWhatsAppError(''); setWhatsAppLoading(true); setWhatsAppReload((value) => value + 1); }}>Retry loading</button>}
        </>}
      </section>
    </div>
  );
}
