import { useEffect, useRef, useState } from 'react';
import { confirmPasswordReset, loginOwner, registerOwner, requestPasswordReset } from '../../api/client.js';

export default function SetupPage({ onAuthenticated }) {
  const [mode, setMode] = useState('login');
  const [name, setName] = useState('');
  const [businessName, setBusinessName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [resetCode, setResetCode] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [notice, setNotice] = useState('');
  const [noticeKind, setNoticeKind] = useState('error');
  const [busy, setBusy] = useState(false);
  const emailInput = useRef(null);
  const resetCodeInput = useRef(null);

  useEffect(() => {
    if (busy) return;
    if (mode === 'reset') resetCodeInput.current?.focus();
    else if (mode === 'login' || mode === 'forgot') emailInput.current?.focus();
  }, [mode, busy]);

  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    setNotice('');
    if (mode === 'reset' && password !== confirmPassword) {
      setNotice('New passwords do not match.');
      setNoticeKind('error');
      return;
    }
    setBusy(true);
    try {
      if (mode === 'forgot') {
        await requestPasswordReset({ email });
        setMode('reset');
        setNotice('If an owner account uses this email, a reset code was sent. Check your inbox.');
        setNoticeKind('success');
      } else if (mode === 'reset') {
        await confirmPasswordReset({ email, code: resetCode.trim(), password });
        setPassword('');
        setConfirmPassword('');
        setResetCode('');
        setMode('login');
        setNotice('Password changed. Log in with your new password.');
        setNoticeKind('success');
      } else {
        const response = mode === 'signup'
          ? await registerOwner({ name, businessName, email, password })
          : await loginOwner({ email, password });
        setPassword('');
        onAuthenticated(response);
      }
    } catch (error) {
      setNotice(error.message);
      setNoticeKind('error');
    } finally {
      setBusy(false);
    }
  }

  function chooseMode(nextMode) {
    if (busy) return;
    setMode(nextMode);
    setNotice('');
    setPassword('');
    setConfirmPassword('');
    setResetCode('');
  }

  return (
    <div className="app-shell">
      <header className="site-header">
        <div className="brand brand--logo"><img src="/shopsync-logo.png" alt="ShopSync AI" /></div>
        <span className="header-context">Owner workspace</span>
      </header>
      <main className="workspace">
        <section className="intro">
          <div className="eyebrow">ACCOUNT SETUP</div>
          <h1>{mode === 'signup' ? 'Set up your business.' : mode === 'forgot' ? 'Recover your account.' : mode === 'reset' ? 'Choose a new password.' : 'Welcome back.'}</h1>
          <p>{mode === 'signup' ? 'Create the owner account for your brand.' : mode === 'forgot' ? 'Enter your owner email to request a reset code.' : mode === 'reset' ? 'Enter the code from your email and create a new password.' : 'Sign in to manage your brand.'}</p>
        </section>
        <section className="side-panel auth-panel" aria-label="Owner account">
          <div className="auth-tabs">
            {mode === 'forgot' || mode === 'reset' ? (
              <button className="button button--quiet" type="button" onClick={() => chooseMode('login')} disabled={busy}>Back to login</button>
            ) : <>
              <button className={`button ${mode === 'login' ? 'button--secondary' : 'button--quiet'}`} type="button" onClick={() => chooseMode('login')} disabled={busy}>Log in</button>
              <button className={`button ${mode === 'signup' ? 'button--secondary' : 'button--quiet'}`} type="button" onClick={() => chooseMode('signup')} disabled={busy}>Create account</button>
            </>}
          </div>
          <form className="auth-form" onSubmit={submit}>
            {mode === 'signup' && <>
              <label htmlFor="owner-name">Your name</label>
              <input id="owner-name" required minLength="2" maxLength="80" autoComplete="name" value={name} onChange={(event) => setName(event.target.value)} disabled={busy} />
              <label htmlFor="brand-name">Business name</label>
              <input id="brand-name" required minLength="2" maxLength="120" autoComplete="organization" value={businessName} onChange={(event) => setBusinessName(event.target.value)} disabled={busy} />
            </>}
            <label htmlFor="owner-email">Email</label>
            <input ref={emailInput} id="owner-email" type="email" required maxLength="254" autoComplete="email" readOnly={mode === 'reset'} value={email} onChange={(event) => setEmail(event.target.value)} disabled={busy} />
            {mode === 'reset' && <>
              <label htmlFor="owner-reset-code">Reset code</label>
              <input ref={resetCodeInput} id="owner-reset-code" type="text" required minLength="32" maxLength="128" autoComplete="one-time-code" value={resetCode} onChange={(event) => setResetCode(event.target.value)} disabled={busy} />
            </>}
            {mode !== 'forgot' && <>
              <label htmlFor="owner-password">{mode === 'reset' ? 'New password' : 'Password'}</label>
              <input id="owner-password" type="password" required minLength={mode === 'login' ? undefined : 12} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} value={password} onChange={(event) => setPassword(event.target.value)} disabled={busy} />
            </>}
            {mode === 'reset' && <>
              <label htmlFor="owner-confirm-password">Confirm new password</label>
              <input id="owner-confirm-password" type="password" required minLength="12" autoComplete="new-password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} disabled={busy} />
            </>}
            {mode === 'signup' && <p>Use at least 12 characters. This account is for the business owner.</p>}
            {mode === 'login' && <button className="auth-link" type="button" onClick={() => chooseMode('forgot')} disabled={busy}>Forgot password?</button>}
            {mode === 'reset' && <button className="auth-link" type="button" onClick={() => chooseMode('forgot')} disabled={busy}>Request a new code</button>}
            <button className="button button--primary" type="submit" disabled={busy}>{busy ? 'Please wait…' : mode === 'signup' ? 'Create owner account' : mode === 'forgot' ? 'Email reset code' : mode === 'reset' ? 'Reset password' : 'Log in'}</button>
          </form>
          {notice && <div className={`auth-notice ${noticeKind === 'success' ? 'auth-notice--success' : ''}`} role={noticeKind === 'success' ? 'status' : 'alert'}>{notice}</div>}
        </section>
      </main>
    </div>
  );
}
