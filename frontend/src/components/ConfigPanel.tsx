import { useEffect, useState } from 'react';
import {
  api,
  type Connection,
  type JsonSchema,
  type PythonTool,
  type Registry,
  type Skill,
  type Spec,
  type SubAgent,
  type Tool,
} from '../api';
import { Problems } from './Problems';
import { PythonToolDialog } from './PythonToolDialog';

type Props = {
  draft: Spec;
  registry: Registry;
  connections: Connection[];
  onChange: (next: Spec) => void;
};

// `resetKey` changes when the draft was replaced from outside: saved, rolled back or discarded.
type PanelProps = Props & { resetKey: number };

const TABS = ['Basics', 'Tools', 'Skills', 'Subagents', 'Guardrails', 'JSON'] as const;
type Tab = (typeof TABS)[number];

const toolName = (tool: Tool) => (tool.kind === 'mcp' ? tool.server : tool.ref);

function Basics({ draft, connections, onChange }: Props) {
  const model = draft.model;
  const chosen = connections.find((c) => c.name === model.connection || c.id === model.connection);

  return (
    <>
      <label className="field">
        คำอธิบาย
        <input value={draft.description} onChange={(e) => onChange({ ...draft, description: e.target.value })} />
      </label>
      <div className="grid-2">
        <label className="field">
          Connection
          <select
            value={chosen?.name ?? ''}
            onChange={(e) => {
              const next = connections.find((c) => c.name === e.target.value);
              onChange({
                ...draft,
                model: { ...model, connection: next?.name ?? null, ref: next?.default_model || model.ref },
              });
            }}
          >
            <option value="">อ่านจาก environment</option>
            {connections.map((c) => (
              <option key={c.id} value={c.name}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Model
          <input
            value={model.ref}
            placeholder={chosen ? 'gpt-4o' : 'anthropic:claude-sonnet-4-6'}
            onChange={(e) => onChange({ ...draft, model: { ...model, ref: e.target.value } })}
          />
        </label>
      </div>
      {model.connection && !chosen && (
        <p className="field-error">ไม่พบ connection ชื่อ {model.connection} ให้สร้างในหน้า Connections ก่อน</p>
      )}
      <label className="field">
        Instructions
        <textarea
          rows={10}
          value={draft.instructions}
          onChange={(e) => onChange({ ...draft, instructions: e.target.value })}
        />
      </label>
      <div className="grid-2">
        <label className="field">
          จำนวน step สูงสุด
          <input
            type="number"
            min={1}
            max={500}
            value={draft.limits.max_steps}
            onChange={(e) =>
              onChange({ ...draft, limits: { ...draft.limits, max_steps: Number(e.target.value) || 1 } })
            }
          />
        </label>
        <label className="field">
          เวลาต่อ run (วินาที)
          <input
            type="number"
            min={5}
            max={3600}
            value={draft.limits.run_timeout_s}
            onChange={(e) =>
              onChange({ ...draft, limits: { ...draft.limits, run_timeout_s: Number(e.target.value) || 5 } })
            }
          />
        </label>
      </div>
    </>
  );
}

function Tools({ draft, registry, onChange }: Props) {
  const [dialog, setDialog] = useState<{ tool?: PythonTool } | null>(null);
  const names = draft.tools.map(toolName);
  const available = registry.tools.filter((t) => !names.includes(t.ref));

  function remove(name: string) {
    const { [name]: _dropped, ...interruptOn } = draft.hitl.interrupt_on;
    onChange({
      ...draft,
      tools: draft.tools.filter((t) => toolName(t) !== name),
      hitl: { interrupt_on: interruptOn },
      subagents: draft.subagents.map((s) => ({ ...s, tools: s.tools.filter((t) => t !== name) })),
    });
  }

  function setApproval(name: string, on: boolean) {
    const { [name]: _dropped, ...rest } = draft.hitl.interrupt_on;
    onChange({ ...draft, hitl: { interrupt_on: on ? { ...rest, [name]: true } : rest } });
  }

  function savePython(tool: PythonTool) {
    const editing = dialog?.tool;
    const tools = editing
      ? draft.tools.map((t) => (toolName(t) === editing.ref ? tool : t))
      : [...draft.tools, tool];
    onChange({ ...draft, tools });
    setDialog(null);
  }

  return (
    <>
      {draft.tools.length === 0 && <p className="muted">agent ตัวนี้ยังไม่มี tool</p>}
      <ul className="rows">
        {draft.tools.map((tool) => {
          const name = toolName(tool);
          return (
            <li key={name}>
              <div className="row-main">
                <strong>{name}</strong> <span className="badge">{tool.kind}</span>
                {tool.kind === 'builtin' && (
                  <p className="muted">{registry.tools.find((t) => t.ref === tool.ref)?.doc}</p>
                )}
                {(tool.kind === 'http' || tool.kind === 'mcp') && (
                  <p className="muted">แก้รายละเอียดได้ใน tab JSON</p>
                )}
              </div>
              {tool.kind !== 'mcp' && (
                <label className="check">
                  <input
                    type="checkbox"
                    checked={draft.hitl.interrupt_on[name] === true}
                    onChange={(e) => setApproval(name, e.target.checked)}
                  />
                  ต้องอนุมัติ
                </label>
              )}
              {tool.kind === 'python' && (
                <button type="button" onClick={() => setDialog({ tool })}>
                  แก้โค้ด
                </button>
              )}
              <button type="button" className="ghost" aria-label={`ลบ ${name}`} onClick={() => remove(name)}>
                ลบ
              </button>
            </li>
          );
        })}
      </ul>
      <div className="row">
        <select
          aria-label="เพิ่ม builtin tool"
          value=""
          onChange={(e) => {
            if (e.target.value) {
              onChange({ ...draft, tools: [...draft.tools, { kind: 'builtin', ref: e.target.value, params: {} }] });
            }
          }}
        >
          <option value="">เพิ่ม builtin tool…</option>
          {available.map((t) => (
            <option key={t.ref} value={t.ref}>
              {t.label}
            </option>
          ))}
        </select>
        <button type="button" onClick={() => setDialog({})}>
          เพิ่ม Python tool
        </button>
      </div>
      {!registry.python_tools.enabled && (
        <p className="muted">Python tool ถูกปิดที่ backend (ต้องตั้ง ALLOW_PYTHON_TOOLS=1)</p>
      )}
      {dialog && (
        <PythonToolDialog
          initial={dialog.tool}
          takenRefs={names.filter((n) => n !== dialog.tool?.ref)}
          onSave={savePython}
          onClose={() => setDialog(null)}
        />
      )}
    </>
  );
}

function Skills({ draft, onChange }: Props) {
  const set = (index: number, patch: Partial<Skill>) =>
    onChange({ ...draft, skills: draft.skills.map((s, i) => (i === index ? { ...s, ...patch } : s)) });

  return (
    <>
      <p className="muted">Playbook แบบ markdown ที่ agent โหลดมาอ่านเมื่อคำอธิบายตรงกับงาน</p>
      {draft.skills.map((skill, index) => (
        <fieldset key={index} className="card">
          <div className="grid-2">
            <label className="field">
              ชื่อ
              <input value={skill.name} onChange={(e) => set(index, { name: e.target.value })} />
            </label>
            <label className="field">
              ใช้เมื่อไร
              <input value={skill.description} onChange={(e) => set(index, { description: e.target.value })} />
            </label>
          </div>
          <label className="field">
            เนื้อหา
            <textarea rows={6} value={skill.body} onChange={(e) => set(index, { body: e.target.value })} />
          </label>
          <button
            type="button"
            className="ghost"
            onClick={() => onChange({ ...draft, skills: draft.skills.filter((_, i) => i !== index) })}
          >
            ลบ skill นี้
          </button>
        </fieldset>
      ))}
      <button
        type="button"
        onClick={() =>
          onChange({
            ...draft,
            skills: [...draft.skills, { name: `skill-${draft.skills.length + 1}`, description: 'ใช้เมื่อ…', body: '# ขั้นตอน\n' }],
          })
        }
      >
        เพิ่ม skill
      </button>
    </>
  );
}

function Subagents({ draft, onChange }: Props) {
  const names = draft.tools.filter((t) => t.kind !== 'mcp').map(toolName);
  const set = (index: number, patch: Partial<SubAgent>) =>
    onChange({ ...draft, subagents: draft.subagents.map((s, i) => (i === index ? { ...s, ...patch } : s)) });

  return (
    <>
      <p className="muted">agent ลูกที่ตัวหลักโยนงานให้ได้ แต่ละตัวมี prompt และชุด tool ของตัวเอง</p>
      {draft.subagents.map((sub, index) => (
        <fieldset key={index} className="card">
          <div className="grid-2">
            <label className="field">
              ชื่อ
              <input value={sub.name} onChange={(e) => set(index, { name: e.target.value })} />
            </label>
            <label className="field">
              โยนงานให้เมื่อไร
              <input value={sub.description} onChange={(e) => set(index, { description: e.target.value })} />
            </label>
          </div>
          <label className="field">
            System prompt
            <textarea
              rows={4}
              value={sub.system_prompt ?? ''}
              onChange={(e) => set(index, { system_prompt: e.target.value || null })}
            />
          </label>
          <div className="row wrap">
            {names.length === 0 && <span className="muted">เพิ่ม tool ใน tab Tools ก่อน</span>}
            {names.map((name) => (
              <label key={name} className="check">
                <input
                  type="checkbox"
                  checked={sub.tools.includes(name)}
                  onChange={(e) =>
                    set(index, {
                      tools: e.target.checked ? [...sub.tools, name] : sub.tools.filter((t) => t !== name),
                    })
                  }
                />
                {name}
              </label>
            ))}
          </div>
          <button
            type="button"
            className="ghost"
            onClick={() => onChange({ ...draft, subagents: draft.subagents.filter((_, i) => i !== index) })}
          >
            ลบ subagent นี้
          </button>
        </fieldset>
      ))}
      <button
        type="button"
        onClick={() =>
          onChange({
            ...draft,
            subagents: [
              ...draft.subagents,
              {
                name: `helper-${draft.subagents.length + 1}`,
                description: 'ใช้เมื่อ…',
                system_prompt: null,
                model: null,
                connection: null,
                tools: [],
                mode: 'isolated',
              },
            ],
          })
        }
      >
        เพิ่ม subagent
      </button>
    </>
  );
}

// One input per property of a middleware's params schema. Anything it cannot draw stays in the JSON tab.
function ParamField({
  name,
  schema,
  value,
  onChange,
}: {
  name: string;
  schema: JsonSchema;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const current = value ?? schema.default;
  if (schema.type === 'boolean') {
    return (
      <label className="check">
        <input type="checkbox" checked={current === true} onChange={(e) => onChange(e.target.checked)} />
        {name}
      </label>
    );
  }
  if (schema.type === 'integer' || schema.type === 'number') {
    return (
      <label className="field">
        {name}
        <input
          type="number"
          min={schema.minimum}
          value={typeof current === 'number' ? current : ''}
          onChange={(e) => onChange(Number(e.target.value))}
        />
      </label>
    );
  }
  if (schema.type === 'array' && schema.items?.enum) {
    const picked = Array.isArray(current) ? (current as string[]) : [];
    return (
      <div className="row wrap" role="group" aria-label={name}>
        <span className="muted">{name}</span>
        {schema.items.enum.map((option) => (
          <label key={option} className="check">
            <input
              type="checkbox"
              checked={picked.includes(option)}
              onChange={(e) =>
                onChange(e.target.checked ? [...picked, option] : picked.filter((p) => p !== option))
              }
            />
            {option}
          </label>
        ))}
      </div>
    );
  }
  if (schema.type === 'array') {
    return (
      <label className="field">
        {name} (คั่นด้วย comma)
        <input
          value={Array.isArray(current) ? current.join(', ') : ''}
          onChange={(e) =>
            onChange(
              e.target.value
                .split(',')
                .map((part) => part.trim())
                .filter(Boolean),
            )
          }
        />
      </label>
    );
  }
  return (
    <label className="field">
      {name}
      <input value={typeof current === 'string' ? current : ''} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

function Guardrails({ draft, registry, onChange }: Props) {
  return (
    <>
      <p className="muted">เลือกจากรายการที่ทีม dev เตรียมไว้ แล้วปรับได้เฉพาะค่า</p>
      {registry.middleware.map((entry) => {
        const active = draft.middleware.find((m) => m.ref === entry.ref);
        const properties = entry.params_schema.properties ?? {};
        return (
          <fieldset key={entry.ref} className="card">
            <label className="check strong">
              <input
                type="checkbox"
                checked={active !== undefined}
                onChange={(e) =>
                  onChange({
                    ...draft,
                    middleware: e.target.checked
                      ? [
                          ...draft.middleware,
                          {
                            ref: entry.ref,
                            // Required params have no usable value until the user fills them in.
                            params: Object.fromEntries(
                              Object.entries(properties).map(([key, schema]) => [key, schema.default ?? []]),
                            ),
                          },
                        ]
                      : draft.middleware.filter((m) => m.ref !== entry.ref),
                  })
                }
              />
              {entry.label}
            </label>
            {active &&
              Object.entries(properties).map(([key, schema]) => (
                <ParamField
                  key={key}
                  name={key}
                  schema={schema}
                  value={active.params[key]}
                  onChange={(value) =>
                    onChange({
                      ...draft,
                      middleware: draft.middleware.map((m) =>
                        m.ref === entry.ref ? { ...m, params: { ...m.params, [key]: value } } : m,
                      ),
                    })
                  }
                />
              ))}
          </fieldset>
        );
      })}
    </>
  );
}

function JsonTab({ draft, onChange }: Props) {
  const [text, setText] = useState(() => JSON.stringify(draft, null, 2));
  const [problems, setProblems] = useState<string[]>([]);
  const [ok, setOk] = useState(false);

  async function apply() {
    setOk(false);
    let parsed: Spec;
    try {
      parsed = JSON.parse(text);
    } catch (error) {
      setProblems([`JSON ไม่ถูกต้อง: ${(error as Error).message}`]);
      return;
    }
    const result = await api.validate(parsed);
    setProblems(result.problems);
    if (result.ok) {
      onChange(parsed);
      setOk(true);
    }
  }

  return (
    <>
      <p className="muted">spec ทั้งก้อนของ agent ตัวนี้ ฟอร์มใน tab อื่นแก้ค่าชุดเดียวกัน</p>
      <textarea
        className="code"
        aria-label="AgentSpec JSON"
        rows={24}
        spellCheck={false}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setOk(false);
        }}
      />
      <Problems items={problems} />
      <div className="row">
        <button type="button" onClick={() => void apply()}>
          ตรวจและใช้ JSON นี้
        </button>
        {ok && <span className="muted">ใช้กับ draft แล้ว ยังไม่ได้บันทึก</span>}
      </div>
    </>
  );
}

export function ConfigPanel({ resetKey, ...props }: PanelProps) {
  const [tab, setTab] = useState<Tab>('Basics');
  // The JSON tab keeps its own text, so remount it whenever another tab changed the draft.
  const [jsonKey, setJsonKey] = useState(0);
  useEffect(() => {
    if (tab !== 'JSON') setJsonKey((key) => key + 1);
  }, [props.draft, tab]);

  const counts: Partial<Record<Tab, number>> = {
    Tools: props.draft.tools.length,
    Skills: props.draft.skills.length,
    Subagents: props.draft.subagents.length,
    Guardrails: props.draft.middleware.length,
  };

  return (
    <section className="config" aria-label="ตั้งค่า agent">
      <div className="tabs" role="tablist">
        {TABS.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={tab === name}
            className={tab === name ? 'tab active' : 'tab'}
            onClick={() => setTab(name)}
          >
            {name}
            {counts[name] ? <span className="count">{counts[name]}</span> : null}
          </button>
        ))}
      </div>
      <div className="tab-body" role="tabpanel">
        {tab === 'Basics' && <Basics {...props} />}
        {tab === 'Tools' && <Tools {...props} />}
        {tab === 'Skills' && <Skills {...props} />}
        {tab === 'Subagents' && <Subagents {...props} />}
        {tab === 'Guardrails' && <Guardrails {...props} />}
        {tab === 'JSON' && <JsonTab key={`${resetKey}-${jsonKey}`} {...props} />}
      </div>
    </section>
  );
}
