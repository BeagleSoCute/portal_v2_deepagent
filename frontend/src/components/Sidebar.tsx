import { useState } from 'react';
import { NavLink } from 'react-router-dom';
import type { Me } from '../api';
import { useAgents } from '../agents';
import { CreateAgent } from './CreateAgent';

export function Sidebar({ me }: { me: Me }) {
  const { agents, problem } = useAgents();
  const [creating, setCreating] = useState(false);

  return (
    <aside className="sidebar">
      <NavLink className="logo" to="/">
        <span className="logo-mark" aria-hidden="true" />
        Agent Builder
      </NavLink>

      <nav className="side-agents" aria-label="Agents">
        <p className="side-label">Agents</p>
        {problem && <p className="field-error">{problem}</p>}
        {agents?.map((agent) => (
          <NavLink key={agent.id} to={`/agents/${agent.id}`} className="side-agent">
            <span className="side-agent-name">{agent.name}</span>
            <span className="side-agent-meta">
              v{agent.version} · {agent.model}
            </span>
          </NavLink>
        ))}
        <button type="button" className="side-create" onClick={() => setCreating(true)}>
          + สร้าง agent
        </button>
      </nav>

      <nav className="side-nav" aria-label="เมนูอื่น">
        <NavLink to="/connections">Connections</NavLink>
        <NavLink to="/audit">Audit</NavLink>
      </nav>

      <div className="side-user">
        {me.auth ? (
          <>
            <span className="avatar" aria-hidden="true">
              {me.username.slice(0, 1).toUpperCase()}
            </span>
            <span className="side-user-name">{me.username}</span>
            {/* A form post, so the browser follows the redirect to Keycloak's logout. */}
            <form method="post" action="/auth/logout">
              <button type="submit" className="ghost">
                ออก
              </button>
            </form>
            <a className="side-portal" href={me.portal_url}>
              กลับ Portal
            </a>
          </>
        ) : (
          <span className="demo-tag" title="ปิด login อยู่ ทุกคนที่เปิดหน้านี้ได้คือผู้ใช้เดียวกัน">
            ไม่มี login
          </span>
        )}
      </div>

      {creating && <CreateAgent onClose={() => setCreating(false)} />}
    </aside>
  );
}
