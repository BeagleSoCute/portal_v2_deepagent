import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, ApiError, messageOf, type Connection, type Registry, type Spec } from '../api';
import { useAgents } from '../agents';
import { Chat } from '../components/Chat';
import { ConfigPanel } from '../components/ConfigPanel';
import { Modal } from '../components/Modal';
import { Problems } from '../components/Problems';

function Versions({
  agentId,
  onActivated,
  onClose,
}: {
  agentId: string;
  onActivated: (spec: Spec) => void;
  onClose: () => void;
}) {
  const [info, setInfo] = useState<{ versions: number[]; head: number } | null>(null);
  const [problem, setProblem] = useState('');

  useEffect(() => {
    api.versions(agentId).then(setInfo).catch((error) => setProblem(messageOf(error)));
  }, [agentId]);

  async function activate(version: number) {
    try {
      onActivated(await api.activate(agentId, version));
    } catch (error) {
      setProblem(messageOf(error));
    }
  }

  return (
    <Modal title="ประวัติเวอร์ชัน" onClose={onClose}>
      <p className="muted">ทุกการบันทึกเป็นเวอร์ชันใหม่ เลือกเวอร์ชันเก่าเพื่อ rollback ได้</p>
      {problem && <Problems items={[problem]} />}
      <ul className="rows">
        {info?.versions
          .slice()
          .reverse()
          .map((version) => (
            <li key={version}>
              <div className="row-main">
                <strong>v{version}</strong> {version === info.head && <span className="badge ok">ใช้งานอยู่</span>}
              </div>
              {version !== info.head && (
                <button type="button" onClick={() => void activate(version)}>
                  ใช้เวอร์ชันนี้
                </button>
              )}
            </li>
          ))}
      </ul>
    </Modal>
  );
}

export function Workbench() {
  const { id = '' } = useParams();
  const navigate = useNavigate();
  const { agents, reload } = useAgents();
  const [saved, setSaved] = useState<Spec | null>(null);
  const [draft, setDraft] = useState<Spec | null>(null);
  const [registry, setRegistry] = useState<Registry | null>(null);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [problems, setProblems] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [showVersions, setShowVersions] = useState(false);
  const [resetKey, setResetKey] = useState(0);

  const load = useCallback((spec: Spec) => {
    setSaved(spec);
    setDraft(spec);
    setProblems([]);
    setResetKey((key) => key + 1);
  }, []);

  useEffect(() => {
    setSaved(null);
    setDraft(null);
    Promise.all([api.agent(id), api.registry(), api.connections()])
      .then(([spec, reg, conns]) => {
        load(spec);
        setRegistry(reg);
        setConnections(conns);
      })
      .catch((error) => setProblems([messageOf(error)]));
  }, [id, load]);

  if (!draft || !saved || !registry) {
    return (
      <main className="page">
        {problems.length > 0 ? <Problems items={problems} /> : <p className="muted">กำลังโหลด…</p>}
      </main>
    );
  }

  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const versions = agents?.find((agent) => agent.id === id)?.versions ?? [saved.version];

  async function save() {
    if (!draft) return;
    setSaving(true);
    try {
      load(await api.saveAgent(id, draft));
      await reload();
    } catch (error) {
      setProblems(error instanceof ApiError ? error.problems : [messageOf(error)]);
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (!saved || !window.confirm(`ลบ agent ${saved.name} และทุกเวอร์ชันของมัน? ลบแล้วกู้คืนไม่ได้`)) return;
    try {
      await api.deleteAgent(id);
      await reload();
      navigate('/');
    } catch (error) {
      setProblems([messageOf(error)]);
    }
  }

  return (
    <main className="workbench">
      <Chat
        key={`chat-${id}`}
        agentId={id}
        draft={draft}
        dirty={dirty}
        title={saved.name}
        subtitle={draft.description}
        version={saved.version}
        needsConnection={connections.length === 0 && !draft.model.connection}
      />
      <ConfigPanel
        key={`settings-${id}`}
        draft={draft}
        saved={saved}
        registry={registry}
        connections={connections}
        resetKey={resetKey}
        nextVersion={Math.max(...versions) + 1}
        saving={saving}
        problems={problems}
        onChange={(next) => {
          setDraft(next);
          setProblems([]);
        }}
        onSave={() => void save()}
        onDiscard={() => load(saved)}
        onVersions={() => setShowVersions(true)}
        onDelete={() => void remove()}
      />
      {showVersions && (
        <Versions
          agentId={id}
          onClose={() => setShowVersions(false)}
          onActivated={(spec) => {
            load(spec);
            setShowVersions(false);
            void reload();
          }}
        />
      )}
    </main>
  );
}
