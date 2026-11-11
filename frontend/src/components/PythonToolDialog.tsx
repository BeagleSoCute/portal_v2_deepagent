import { useState } from 'react';
import { api, messageOf, type PythonTool } from '../api';
import { Modal } from './Modal';
import { Problems } from './Problems';

const TEMPLATE = `def run(name: str, times: int = 1) -> dict:
    """ทักทายตามชื่อที่ส่งมา

    agent อ่าน docstring นี้เพื่อตัดสินใจว่าจะเรียก tool เมื่อไร
    """
    return {"greeting": " ".join(["สวัสดี " + name] * times)}
`;

const SLUG = /^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/;

export function PythonToolDialog({
  initial,
  takenRefs,
  onSave,
  onClose,
}: {
  initial?: PythonTool;
  takenRefs: string[];
  onSave: (tool: PythonTool) => void;
  onClose: () => void;
}) {
  const [ref, setRef] = useState(initial?.ref ?? '');
  const [code, setCode] = useState(initial?.code ?? TEMPLATE);
  const [kwargs, setKwargs] = useState(initial ? '{}' : '{"name": "ทีม demo"}');
  const [problems, setProblems] = useState<string[]>([]);
  const [output, setOutput] = useState('');
  const [busy, setBusy] = useState(false);

  async function check(): Promise<boolean> {
    setBusy(true);
    setOutput('');
    try {
      const result = await api.checkPython(code);
      if (!result.enabled) {
        setProblems(['Python tool ถูกปิดอยู่ ให้ตั้ง ALLOW_PYTHON_TOOLS=1 ที่ backend ก่อน']);
        return false;
      }
      setProblems(result.problems);
      if (result.ok) {
        setOutput(`สิ่งที่ agent จะเห็น\n\n${result.description}\n\n${JSON.stringify(result.input_schema, null, 2)}`);
      }
      return result.ok;
    } catch (error) {
      setProblems([messageOf(error)]);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function tryRun() {
    let args: Record<string, unknown>;
    try {
      args = JSON.parse(kwargs);
    } catch {
      setProblems(['argument ต้องเป็น JSON ที่ถูกต้อง']);
      return;
    }
    setBusy(true);
    setProblems([]);
    try {
      setOutput(JSON.stringify(await api.runPython(code, args, 15), null, 2));
    } catch (error) {
      setProblems([messageOf(error)]);
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!SLUG.test(ref)) {
      setProblems(['ชื่อ tool ต้องขึ้นต้นด้วยตัวอักษร และใช้ได้แค่ a-z, 0-9, _ และ -']);
      return;
    }
    if (takenRefs.includes(ref)) {
      setProblems([`มี tool ชื่อ ${ref} อยู่แล้ว`]);
      return;
    }
    if (!(await check())) return;
    onSave({
      kind: 'python',
      ref,
      description: initial?.description ?? '',
      code,
      input_schema: null,
      timeout_s: initial?.timeout_s ?? 15,
    });
  }

  return (
    <Modal title={initial ? `แก้ Python tool: ${initial.ref}` : 'เพิ่ม Python tool'} wide onClose={onClose}>
      <label className="field">
        ชื่อ tool
        <input value={ref} placeholder="price_quote" onChange={(e) => setRef(e.target.value)} />
      </label>
      <label className="field">
        โค้ด (ต้องมี <code>def run(...)</code> พร้อม docstring)
        <textarea
          className="code"
          rows={14}
          spellCheck={false}
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
      </label>
      <label className="field">
        argument สำหรับลองรัน (JSON)
        <input className="code" value={kwargs} onChange={(e) => setKwargs(e.target.value)} />
      </label>
      <Problems items={problems} />
      {output && <pre className="output">{output}</pre>}
      <div className="row end">
        <button type="button" disabled={busy} onClick={() => void check()}>
          ตรวจโค้ด
        </button>
        <button type="button" disabled={busy} onClick={() => void tryRun()}>
          ลองรัน
        </button>
        <button type="button" className="primary" disabled={busy} onClick={() => void save()}>
          {initial ? 'อัปเดต tool' : 'เพิ่มเข้า agent'}
        </button>
      </div>
    </Modal>
  );
}
