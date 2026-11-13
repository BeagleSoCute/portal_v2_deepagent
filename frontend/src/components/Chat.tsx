import { useEffect, useRef, useState, type FormEvent } from 'react';
import Markdown from 'react-markdown';
import { Link } from 'react-router-dom';
import { messageOf, streamRun, type ActionRequest, type RunEvent, type Spec } from '../api';

type Item =
  | { type: 'user'; text: string }
  | { type: 'assistant'; text: string }
  | { type: 'tool'; id: string; name: string; args: Record<string, unknown>; result?: string }
  | { type: 'approval'; actions: ActionRequest[]; state: 'pending' | 'approved' | 'rejected' | 'edited' }
  | { type: 'error'; text: string }
  | { type: 'meta'; text: string };

const newThread = () => `ui-${Math.random().toString(36).slice(2, 10)}`;

// A tool returns a bare path like /downloads/report.pdf, in prose or inside a code block.
// Collect them so each gets a link under the message, wherever the model wrote it.
function downloadsIn(text: string): string[] {
  return [...new Set(text.match(/\/downloads\/[^\s)"'`<>\]]+/g) ?? [])];
}

function applyEvent(items: Item[], event: RunEvent): Item[] {
  const last = items[items.length - 1];
  switch (event.event) {
    case 'start':
      return [...items, { type: 'meta', text: `${event.data.agent} v${event.data.version} · ${event.data.model}` }];
    case 'token': {
      const { text, node } = event.data;
      if (node === 'model' || node === undefined) {
        if (last?.type === 'assistant') return [...items.slice(0, -1), { ...last, text: last.text + text }];
        return [...items, { type: 'assistant', text }];
      }
      // Tool output and the note left by a rejected call both belong to the open tool call.
      if (node === 'tools' || node.startsWith('HumanInTheLoop')) {
        const index = items.findLastIndex((item) => item.type === 'tool' && item.result === undefined);
        if (index < 0) return items;
        const tool = items[index] as Extract<Item, { type: 'tool' }>;
        return [...items.slice(0, index), { ...tool, result: text }, ...items.slice(index + 1)];
      }
      return items;
    }
    case 'tool_calls': {
      // On resume the approval step replays the same calls; keep only the model's own.
      const known = new Set(items.flatMap((item) => (item.type === 'tool' ? [item.id] : [])));
      const fresh = event.data.calls.filter((c) => !known.has(c.id));
      return [...items, ...fresh.map((c) => ({ type: 'tool' as const, id: c.id, name: c.name, args: c.args }))];
    }
    case 'interrupt': {
      const actions = event.data.payload.flatMap((p) => p.value?.action_requests ?? []);
      return [...items, { type: 'approval', actions, state: 'pending' }];
    }
    case 'error':
      return [...items, { type: 'error', text: event.data.message }];
    case 'done':
      return [...items, { type: 'meta', text: `เสร็จใน ${event.data.elapsed_s} วินาที` }];
    default:
      return items;
  }
}

function Approval({
  item,
  busy,
  onDecide,
}: {
  item: Extract<Item, { type: 'approval' }>;
  busy: boolean;
  onDecide: (decision: 'accept' | 'reject' | 'edit', editedArgs?: Record<string, unknown>) => void;
}) {
  const first = item.actions[0];
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(JSON.stringify(first?.args ?? {}, null, 2));
  const [problem, setProblem] = useState('');

  if (item.state !== 'pending') {
    const label = { approved: 'อนุมัติแล้ว', rejected: 'ปฏิเสธแล้ว', edited: 'แก้ค่าแล้วอนุมัติ' }[item.state];
    return <p className="chat-meta">{label}: {item.actions.map((a) => a.name).join(', ')}</p>;
  }

  function submitEdit() {
    try {
      onDecide('edit', JSON.parse(draft));
    } catch {
      setProblem('args ต้องเป็น JSON ที่ถูกต้อง');
    }
  }

  return (
    <div className="approval">
      {item.actions.map((action, index) => (
        <div key={index}>
          <strong>ขออนุมัติก่อนเรียก {action.name}</strong>
          <dl className="args">
            {Object.entries(action.args).map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{typeof value === 'string' ? value : JSON.stringify(value)}</dd>
              </div>
            ))}
            {Object.keys(action.args).length === 0 && <dd className="muted">ไม่มี argument</dd>}
          </dl>
        </div>
      ))}
      {editing && (
        <>
          <textarea
            aria-label="args ที่แก้แล้ว"
            rows={4}
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setProblem('');
            }}
          />
          {problem && <p className="field-error">{problem}</p>}
        </>
      )}
      <div className="row">
        {editing ? (
          <>
            <button type="button" className="primary" disabled={busy} onClick={submitEdit}>
              อนุมัติด้วยค่านี้
            </button>
            <button type="button" onClick={() => setEditing(false)}>
              ยกเลิก
            </button>
          </>
        ) : (
          <>
            <button type="button" className="primary" disabled={busy} onClick={() => onDecide('accept')}>
              อนุมัติ
            </button>
            <button type="button" disabled={busy} onClick={() => setEditing(true)}>
              แก้ค่า
            </button>
            <button type="button" disabled={busy} onClick={() => onDecide('reject')}>
              ปฏิเสธ
            </button>
          </>
        )}
      </div>
    </div>
  );
}

export function Chat({
  agentId,
  draft,
  dirty,
  title,
  subtitle,
  version,
  needsConnection,
}: {
  agentId: string;
  draft: Spec;
  dirty: boolean;
  title: string;
  subtitle: string;
  version: number;
  /** No LLM is set up yet, so a message could only fail. */
  needsConnection: boolean;
}) {
  const [items, setItems] = useState<Item[]>([]);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [thread, setThread] = useState(newThread);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (items.length > 0) bottom.current?.scrollIntoView({ block: 'end' });
  }, [items]);

  useEffect(() => () => abort.current?.abort(), []);

  // Unsaved edits are sent along, so a change can be tried before it becomes a version.
  const target = dirty ? { spec: draft } : { agent_id: agentId };
  const waiting = items.some((item) => item.type === 'approval' && item.state === 'pending');

  async function run(path: '/api/runs/stream' | '/api/runs/resume', body: Record<string, unknown>) {
    setBusy(true);
    abort.current = new AbortController();
    try {
      await streamRun(
        path,
        { ...target, thread_id: thread, ...body },
        (event) => setItems((current) => applyEvent(current, event)),
        abort.current.signal,
      );
    } catch (error) {
      if (!abort.current.signal.aborted) {
        setItems((current) => [...current, { type: 'error', text: messageOf(error) }]);
      }
    } finally {
      setBusy(false);
    }
  }

  function send(event: FormEvent) {
    event.preventDefault();
    const message = text.trim();
    if (!message || busy || waiting) return;
    setText('');
    setItems((current) => [...current, { type: 'user', text: message }]);
    void run('/api/runs/stream', { message });
  }

  function decide(decision: 'accept' | 'reject' | 'edit', editedArgs?: Record<string, unknown>) {
    const state = ({ accept: 'approved', reject: 'rejected', edit: 'edited' } as const)[decision];
    setItems((current) =>
      current.map((item) => (item.type === 'approval' && item.state === 'pending' ? { ...item, state } : item)),
    );
    void run('/api/runs/resume', { decision, edited_args: editedArgs });
  }

  function reset() {
    abort.current?.abort();
    setItems([]);
    setThread(newThread());
  }

  return (
    <section className="chat" aria-label="ทดลองแชท">
      <header className="chat-head">
        <div className="chat-title">
          <h1>{title}</h1>
          <span className="badge">v{version}</span>
          {dirty && <span className="badge warn">กำลังลอง draft ที่ยังไม่บันทึก</span>}
          <p className="muted">{subtitle}</p>
        </div>
        <button type="button" onClick={reset}>
          ห้องใหม่
        </button>
      </header>

      <div className="chat-log" aria-live="polite">
        {needsConnection && (
          <p className="notice">
            ยังไม่มี LLM connection agent จะตอบไม่ได้จนกว่าจะ <Link to="/connections">เพิ่ม connection</Link>
          </p>
        )}
        {items.length === 0 && (
          <div className="chat-empty">
            <p className="chat-empty-title">ลองคุยกับ {title}</p>
            <p className="muted">การแก้ค่าทางขวามีผลกับข้อความถัดไปทันที ยังไม่ต้องบันทึก</p>
          </div>
        )}
        {items.map((item, index) => {
          if (item.type === 'user') return <div key={index} className="bubble user">{item.text}</div>;
          if (item.type === 'assistant') {
            return (
              <div key={index} className="bubble assistant">
                <Markdown>{item.text}</Markdown>
                {downloadsIn(item.text).map((path) => (
                  <a key={path} className="download" href={path} target="_blank" rel="noreferrer">
                    ดาวน์โหลด {decodeURIComponent(path.split('/').pop() ?? '')}
                  </a>
                ))}
              </div>
            );
          }
          if (item.type === 'tool') {
            return (
              <details key={index} className="tool-call">
                <summary>
                  <span className={item.result === undefined ? 'pulse' : 'pulse done'} aria-hidden="true" />
                  {item.result === undefined ? 'กำลังเรียก' : 'เรียก'} <code>{item.name}</code>
                </summary>
                <pre>{JSON.stringify(item.args, null, 2)}</pre>
                {item.result !== undefined && <pre className="result">{item.result}</pre>}
              </details>
            );
          }
          if (item.type === 'approval') {
            return <Approval key={index} item={item} busy={busy} onDecide={decide} />;
          }
          if (item.type === 'error') return <p key={index} className="problems" role="alert">{item.text}</p>;
          return <p key={index} className="chat-meta">{item.text}</p>;
        })}
        {busy && <p className="chat-meta">กำลังทำงาน…</p>}
        <div ref={bottom} />
      </div>

      <form className="chat-input" onSubmit={send}>
        <div className="composer">
          <input
            aria-label="ข้อความถึง agent"
            placeholder={waiting ? 'ตัดสินใจเรื่องการอนุมัติก่อน' : `ถาม ${title}`}
            value={text}
            disabled={waiting}
            onChange={(e) => setText(e.target.value)}
          />
          <button type="submit" className="send" aria-label="ส่ง" disabled={busy || waiting || !text.trim()}>
            ↑
          </button>
        </div>
      </form>
    </section>
  );
}
