import { useEffect, useState, type FormEvent } from 'react';
import { api, messageOf, type Connection, type ConnectionInput, type Registry } from '../api';
import { Problems } from '../components/Problems';

const EMPTY: ConnectionInput = { name: '', kind: 'openai_compatible', base_url: '', default_model: '', api_key: '' };

export function Connections() {
  const [connections, setConnections] = useState<Connection[] | null>(null);
  const [kinds, setKinds] = useState<Registry['connection_kinds']>([]);
  const [form, setForm] = useState<ConnectionInput>(EMPTY);
  const [problems, setProblems] = useState<string[]>([]);
  const [notes, setNotes] = useState<Record<string, string>>({});

  const load = () => api.connections().then(setConnections);

  useEffect(() => {
    Promise.all([load(), api.registry().then((r) => setKinds(r.connection_kinds))]).catch((error) =>
      setProblems([messageOf(error)]),
    );
  }, []);

  async function add(input: ConnectionInput) {
    setProblems([]);
    try {
      await api.createConnection({ ...input, base_url: input.base_url || null, api_key: input.api_key || null });
      setForm(EMPTY);
      await load();
    } catch (error) {
      setProblems(messageOf(error).split('\n'));
    }
  }

  async function act(id: string, action: () => Promise<string>) {
    setNotes((current) => ({ ...current, [id]: 'กำลังทำงาน…' }));
    try {
      const note = await action();
      setNotes((current) => ({ ...current, [id]: note }));
    } catch (error) {
      setNotes((current) => ({ ...current, [id]: messageOf(error) }));
    }
  }

  const needsKey = form.kind !== 'env' && form.kind !== 'mock';

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Connections</h1>
          <p className="muted">ที่อยู่ของ LLM ที่ agent ใช้ API key ถูกเข้ารหัสก่อนเก็บ และไม่ถูกส่งกลับมาที่ browser</p>
        </div>
        <button
          type="button"
          onClick={() => void add({ name: 'demo-mock', kind: 'mock', base_url: null, default_model: 'mock' })}
        >
          เพิ่ม mock connection สำหรับ demo
        </button>
      </div>

      {connections?.length === 0 && <p className="muted">ยังไม่มี connection</p>}
      <ul className="rows">
        {connections?.map((c) => (
          <li key={c.id}>
            <div className="row-main">
              <strong>{c.name}</strong> <span className="badge">{c.kind}</span>
              <p className="muted">
                {c.base_url ?? c.kind_label} · model {c.default_model || 'ยังไม่ระบุ'}
                {c.has_key && ` · key ${c.api_key_masked}`}
              </p>
              {notes[c.id] && <p className="note">{notes[c.id]}</p>}
            </div>
            <button
              type="button"
              onClick={() =>
                void act(c.id, async () => {
                  const result = await api.testConnection(c.id);
                  return result.ok
                    ? `ใช้ได้ ตอบใน ${result.elapsed_s} วินาที: ${result.reply_preview ?? ''}`
                    : `ใช้ไม่ได้: ${result.error}`;
                })
              }
            >
              ทดสอบ
            </button>
            <button
              type="button"
              onClick={() => {
                if (!window.confirm(`ตั้ง ${c.name} ให้ agent ทุกตัว? แต่ละตัวจะถูกบันทึกเป็นเวอร์ชันใหม่`)) return;
                void act(c.id, async () => {
                  const result = await api.applyConnection(c.id);
                  return `ตั้งให้ agent ${result.updated.length} ตัวแล้ว`;
                });
              }}
            >
              ใช้กับ agent ทั้งหมด
            </button>
            <button
              type="button"
              className="ghost"
              onClick={() => {
                if (!window.confirm(`ลบ connection ${c.name}? agent ที่ใช้อยู่จะตอบไม่ได้`)) return;
                void api.deleteConnection(c.id).then(load).catch((error) => setProblems([messageOf(error)]));
              }}
            >
              ลบ
            </button>
          </li>
        ))}
      </ul>

      <form
        className="card"
        onSubmit={(event: FormEvent) => {
          event.preventDefault();
          void add(form);
        }}
      >
        <h2>เพิ่ม connection</h2>
        <div className="grid-2">
          <label className="field">
            ชื่อ
            <input value={form.name} required onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </label>
          <label className="field">
            ชนิด
            <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              {kinds.map((k) => (
                <option key={k.ref} value={k.ref}>
                  {k.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Base URL
            <input
              value={form.base_url ?? ''}
              placeholder="https://gateway.example.com/v1"
              disabled={!needsKey}
              onChange={(e) => setForm({ ...form, base_url: e.target.value })}
            />
          </label>
          <label className="field">
            Model เริ่มต้น
            <input
              value={form.default_model}
              placeholder="gpt-4o"
              onChange={(e) => setForm({ ...form, default_model: e.target.value })}
            />
          </label>
        </div>
        {needsKey && (
          <label className="field">
            API key
            <input
              type="password"
              autoComplete="off"
              value={form.api_key ?? ''}
              onChange={(e) => setForm({ ...form, api_key: e.target.value })}
            />
          </label>
        )}
        <Problems items={problems} />
        <div className="row end">
          <button type="submit" className="primary">
            บันทึก connection
          </button>
        </div>
      </form>
    </main>
  );
}
