import { useEffect, useState } from 'react';
import { NavLink, Route, Routes } from 'react-router-dom';
import { api, messageOf, Redirecting, type Me } from './api';
import { AgentList } from './pages/AgentList';
import { Audit } from './pages/Audit';
import { Connections } from './pages/Connections';
import { Workbench } from './pages/Workbench';

function Topbar({ me }: { me?: Me }) {
  return (
    <header className="topbar">
      <NavLink className="logo" to="/">
        <span className="logo-mark" aria-hidden="true" />
        Agent Builder
      </NavLink>
      {me && (
        <>
          <nav className="topnav" aria-label="เมนูหลัก">
            <NavLink to="/" end>
              Agents
            </NavLink>
            <NavLink to="/connections">Connections</NavLink>
            <NavLink to="/audit">Audit</NavLink>
          </nav>
          {me.auth ? (
            <div className="row">
              <a href={me.portal_url}>กลับ Portal</a>
              <span className="muted">{me.username}</span>
              {/* A form post, so the browser follows the redirect to Keycloak's logout. */}
              <form method="post" action="/auth/logout">
                <button type="submit">ออกจากระบบ</button>
              </form>
            </div>
          ) : (
            <span className="demo-tag" title="ปิด login อยู่ ทุกคนที่เปิดหน้านี้ได้คือผู้ใช้เดียวกัน">
              ไม่มี login
            </span>
          )}
        </>
      )}
    </header>
  );
}

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
      <>
        <Topbar />
        <p className="page problems" role="alert">
          ระบบขัดข้อง กรุณาลองใหม่อีกครั้ง ({problem})
        </p>
      </>
    );
  }
  if (!me) return <Topbar />;

  return (
    <>
      <Topbar me={me} />
      <Routes>
        <Route path="/" element={<AgentList />} />
        <Route path="/agents/:id" element={<Workbench />} />
        <Route path="/connections" element={<Connections />} />
        <Route path="/audit" element={<Audit />} />
        <Route path="*" element={<p className="page muted">ไม่พบหน้านี้</p>} />
      </Routes>
    </>
  );
}

function LoggedOut() {
  return (
    <>
      <Topbar />
      <main className="page">
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
