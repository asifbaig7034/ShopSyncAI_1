import OwnerChatPage from './features/assistant/OwnerChatPage.jsx';
import SetupPage from './features/setup/SetupPage.jsx';
import { useOwnerSession } from './auth/OwnerSession.jsx';

export default function App() {
  const { session, authenticate, signOut, updateOwnerProfile } = useOwnerSession();
  return session
    ? <OwnerChatPage key={session.owner.id} session={session} onLogout={signOut} onProfileSaved={updateOwnerProfile} />
    : <SetupPage onAuthenticated={authenticate} />;
}
