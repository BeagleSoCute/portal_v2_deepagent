import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, ApiError, messageOf, type Connection, type Registry, type Spec } from '../api';
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

  async function save() {
    if (!draft) return;
    setSaving(true);
    try {
      load(await api.saveAgent(id, draft));
    } catch (error) {
      setProblems(error instanceof ApiError ? error.problems : [messageOf(error)]);
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="workbench">
      <div className="workbench-head">
        <div>
          <Link to="/" className="muted">
            Agents
          </Link>
          <span className="muted"> / </span>
          <h1>{saved.name}</h1>
          <span className="badge ok">v{saved.version}</span>
          {dirty && <span className="badge warn">มีการแก้ที่ยังไม่บันทึก</span>}
        </div>
        <div className="row">
          <button type="button" onClick={() => setShowVersions(true)}>
            เวอร์ชัน
          </button>
          {dirty && (
            <button type="button" onClick={() => load(saved)}>
              ยกเลิกการแก้
            </button>
          )}
          <button type="button" className="primary" disabled={!dirty || saving} onClick={() => void save()}>
            บันทึกเป็นเวอร์ชันใหม่
          </button>
        </div>
      </div>
      <Problems items={problems} />
      <div className="workbench-body">
        <ConfigPanel
          resetKey={resetKey}
          draft={draft}
          registry={registry}
          connections={connections}
          onChange={(next) => {
            setDraft(next);
            setProblems([]);
          }}
        />
        <Chat key={id} agentId={id} draft={draft} dirty={dirty} />
      </div>
      {showVersions && (
        <Versions
          agentId={id}
          onClose={() => setShowVersions(false)}
          onActivated={(spec) => {
            load(spec);
            setShowVersions(false);
          }}
        />
      )}
    </main>
  );
}
