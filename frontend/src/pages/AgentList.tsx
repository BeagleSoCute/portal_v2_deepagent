import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, messageOf, type AgentRow, type Connection } from '../api';
import { Modal } from '../components/Modal';
import { Problems } from '../components/Problems';

function CreateAgent({ connections, onClose }: { connections: Connection[]; onClose: () => void }) {
  const navigate = useNavigate();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [problems, setProblems] = useState<string[]>([]);

  async function create(event: FormEvent) {
    event.preventDefault();
    const connection = connections[0];
    try {
      const spec = await api.createAgent({
        name,
        description,
        instructions: 'คุณคือผู้ช่วยที่ตอบสั้น กระชับ และตรงคำถาม',
        model: {
          connection: connection?.name ?? null,
          ref: connection?.default_model || 'anthropic:claude-sonnet-4-6',
          temperature: null,
          max_tokens: null,
        },
      });
      navigate(`/agents/${spec.id}`);
    } catch (error) {
      setProblems(messageOf(error).split('\n'));
    }
  }

  return (
    <Modal title="สร้าง agent" onClose={onClose}>
      <form onSubmit={create}>
        <label className="field">
          ชื่อ (a-z, 0-9, _ และ -)
          <input value={name} placeholder="sales-helper" required onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          คำอธิบาย
          <input value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <Problems items={problems} />
        <div className="row end">
          <button type="button" onClick={onClose}>
            ยกเลิก
          </button>
          <button type="submit" className="primary">
            สร้าง agent
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function AgentList() {
  const [agents, setAgents] = useState<AgentRow[] | null>(null);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [problem, setProblem] = useState('');
  const [creating, setCreating] = useState(false);

  const load = () =>
    Promise.all([api.agents(), api.connections()])
      .then(([rows, conns]) => {
        setAgents(rows);
        setConnections(conns);
      })
      .catch((error) => setProblem(messageOf(error)));

  useEffect(() => {
    void load();
  }, []);

  async function remove(agent: AgentRow) {
    if (!window.confirm(`ลบ agent ${agent.name} และทุกเวอร์ชันของมัน? ลบแล้วกู้คืนไม่ได้`)) return;
    try {
      await api.deleteAgent(agent.id);
      await load();
    } catch (error) {
      setProblem(messageOf(error));
    }
  }

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Agents</h1>
          <p className="muted">agent แต่ละตัวคือ config หนึ่งชุด แก้แล้วมีผลทันทีโดยไม่ต้อง deploy</p>
        </div>
        <button type="button" className="primary" onClick={() => setCreating(true)}>
          สร้าง agent
        </button>
      </div>
      {problem && <Problems items={[problem]} />}
      {agents && connections.length === 0 && (
        <p className="notice">
          ยังไม่มี LLM connection agent จะตอบไม่ได้จนกว่าจะ <Link to="/connections">เพิ่ม connection</Link>
        </p>
      )}
      {agents?.length === 0 && <p className="muted">ยังไม่มี agent เริ่มจากสร้างตัวแรก</p>}
      {agents && agents.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>ชื่อ</th>
              <th>คำอธิบาย</th>
              <th>Model</th>
              <th>เวอร์ชัน</th>
              <th>แก้ล่าสุด</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {agents.map((agent) => (
              <tr key={agent.id}>
                <td>
                  <Link to={`/agents/${agent.id}`}>
                    <strong>{agent.name}</strong>
                  </Link>
                </td>
                <td className="muted">{agent.description}</td>
                <td>{agent.model}</td>
                <td>
                  v{agent.version} <span className="muted">จาก {agent.versions.length}</span>
                </td>
                <td className="muted">{new Date(agent.updated_at).toLocaleString('th-TH')}</td>
                <td>
                  <button type="button" className="ghost" onClick={() => void remove(agent)}>
                    ลบ
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {creating && <CreateAgent connections={connections} onClose={() => setCreating(false)} />}
    </main>
  );
}
