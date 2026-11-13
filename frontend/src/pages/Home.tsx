import { useState } from 'react';
import { Navigate } from 'react-router-dom';
import { useAgents } from '../agents';
import { CreateAgent } from '../components/CreateAgent';

// The first agent is the landing page. Without one, the only thing to do is create it.
export function Home() {
  const { agents } = useAgents();
  const [creating, setCreating] = useState(false);

  if (!agents) return null;
  if (agents.length > 0) return <Navigate to={`/agents/${agents[0].id}`} replace />;

  return (
    <main className="page empty-state">
      <h1>สร้าง agent ตัวแรก</h1>
      <p className="muted">agent แต่ละตัวคือ config หนึ่งชุด แก้แล้วลองแชทได้ทันทีโดยไม่ต้อง deploy</p>
      <button type="button" className="primary" onClick={() => setCreating(true)}>
        สร้าง agent
      </button>
      {creating && <CreateAgent onClose={() => setCreating(false)} />}
    </main>
  );
}
