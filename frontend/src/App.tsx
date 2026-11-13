import { useEffect, useState } from 'react';
import { Route, Routes } from 'react-router-dom';
import { api, messageOf, Redirecting, type Me } from './api';
import { AgentsProvider } from './agents';
import { Sidebar } from './components/Sidebar';
import { Audit } from './pages/Audit';
import { Connections } from './pages/Connections';
import { Home } from './pages/Home';
import { Workbench } from './pages/Workbench';

function SignedIn() {
  const [me, setMe] = useState<Me | null>(null);
  const [problem, setProblem] = useState('');

  useEffect(() => {
    api
      .me()
      .then(setMe)
      .catch((error) => {
        if (!(error instanceof Redirecting)) setProblem(messageOf(error));
      });
  }, []);

  if (problem) {
    return (
      <p className="page problems" role="alert">
        ระบบขัดข้อง กรุณาลองใหม่อีกครั้ง ({problem})
      </p>
    );
  }
  if (!me) return null;

  return (
    <AgentsProvider>
      <div className="shell">
        <Sidebar me={me} />
        <div className="shell-main">
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/agents/:id" element={<Workbench />} />
            <Route path="/connections" element={<Connections />} />
            <Route path="/audit" element={<Audit />} />
            <Route path="*" element={<p className="page muted">ไม่พบหน้านี้</p>} />
          </Routes>
        </div>
      </div>
    </AgentsProvider>
  );
}

function LoggedOut() {
  return (
    <>
      <main className="page empty-state">
        <h1>ออกจากระบบแล้ว</h1>
        <p className="muted">session ของ Agent Builder และ Portal จบแล้ว</p>
        <p style={{ marginTop: 16 }}>
          <a href="/auth/login">เข้าสู่ระบบอีกครั้ง</a>
        </p>
      </main>
    </>
  );
}

export function App() {
  return (
    <Routes>
      {/* Reached after logout, so it must not ask who is signed in. */}
      <Route path="/logged-out" element={<LoggedOut />} />
      <Route path="*" element={<SignedIn />} />
    </Routes>
  );
}
