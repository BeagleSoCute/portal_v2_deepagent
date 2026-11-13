import { useState, type ReactNode } from 'react';
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
import { Modal } from './Modal';
import { Problems } from './Problems';
import { PythonToolDialog } from './PythonToolDialog';

type Props = {
  draft: Spec;
  registry: Registry;
  connections: Connection[];
  onChange: (next: Spec) => void;
};

const toolName = (tool: Tool) => (tool.kind === 'mcp' ? tool.server : tool.ref);

function ModelSection({ draft, connections, onChange }: Props) {
  const model = draft.model;
  const chosen = connections.find((c) => c.name === model.connection || c.id === model.connection);

  return (
    <>
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
      {model.connection && !chosen && (
        <p className="field-error">ไม่พบ connection ชื่อ {model.connection} ให้สร้างในหน้า Connections ก่อน</p>
      )}
    </>
  );
}

function InstructionsSection({ draft, onChange }: Props) {
  return (
    <>
      <label className="field">
        คำอธิบายสั้น
        <input value={draft.description} onChange={(e) => onChange({ ...draft, description: e.target.value })} />
      </label>
      <label className="field">
        Instructions
        <textarea
          rows={12}
          value={draft.instructions}
          onChange={(e) => onChange({ ...draft, instructions: e.target.value })}
        />
      </label>
    </>
  );
}

function LimitsSection({ draft, onChange }: Props) {
  return (
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
                  <p className="muted">แก้รายละเอียดได้ใน JSON</p>
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
            {names.length === 0 && <span className="muted">เพิ่ม tool ในการ์ด Tools ก่อน</span>}
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
      <p className="muted">spec ทั้งก้อนของ agent ตัวนี้ การ์ดตั้งค่าแก้ค่าชุดเดียวกัน</p>
      <textarea
        className="code"
        aria-label="AgentSpec JSON"
        rows={22}
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

type SectionId = 'model' | 'instructions' | 'tools' | 'guardrails' | 'skills' | 'subagents' | 'limits';

type Section = {
  id: SectionId;
  title: string;
  /** The parts of the spec this card edits, to tell whether it holds unsaved changes. */
  keys: (keyof Spec)[];
  summary: (props: Props) => ReactNode;
  Body: (props: Props) => ReactNode;
};

const firstLine = (text: string) => text.split('\n').find((line) => line.trim())?.trim() ?? '';
const none = <span className="muted">ยังไม่มี</span>;

const SECTIONS: Section[] = [
  {
    id: 'model',
    title: 'Model',
    keys: ['model'],
    summary: ({ draft }) => `${draft.model.connection ?? 'environment'} · ${draft.model.ref}`,
    Body: ModelSection,
  },
  {
    id: 'instructions',
    title: 'Instructions',
    keys: ['instructions', 'description'],
    summary: ({ draft }) => firstLine(draft.instructions) || none,
    Body: InstructionsSection,
  },
  {
    id: 'tools',
    title: 'Tools',
    keys: ['tools', 'hitl'],
    summary: ({ draft }) =>
      draft.tools.length === 0
        ? none
        : draft.tools.map((tool) => {
            const name = toolName(tool);
            return (
              <span key={name} className="summary-line">
                {name}
                {draft.hitl.interrupt_on[name] ? (
                  <span className="badge warn">ต้องอนุมัติ</span>
                ) : (
                  <span className="muted">อัตโนมัติ</span>
                )}
              </span>
            );
          }),
    Body: Tools,
  },
  {
    id: 'guardrails',
    title: 'Guardrails',
    keys: ['middleware'],
    summary: ({ draft, registry }) =>
      draft.middleware.length === 0
        ? none
        : draft.middleware.map((m) => (
            <span key={m.ref} className="badge ok">
              {registry.middleware.find((entry) => entry.ref === m.ref)?.label ?? m.ref}
            </span>
          )),
    Body: Guardrails,
  },
  {
    id: 'skills',
    title: 'Skills',
    keys: ['skills'],
    summary: ({ draft }) => draft.skills.map((s) => s.name).join(', ') || none,
    Body: Skills,
  },
  {
    id: 'subagents',
    title: 'Subagents',
    keys: ['subagents'],
    summary: ({ draft }) => draft.subagents.map((s) => s.name).join(', ') || none,
    Body: Subagents,
  },
  {
    id: 'limits',
    title: 'ขีดจำกัด',
    keys: ['limits'],
    summary: ({ draft }) => `${draft.limits.max_steps} step · ${draft.limits.run_timeout_s} วินาที`,
    Body: LimitsSection,
  },
];

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

export function ConfigPanel({
  saved,
  resetKey,
  nextVersion,
  saving,
  problems,
  onSave,
  onDiscard,
  onVersions,
  onDelete,
  ...props
}: Props & {
  saved: Spec;
  /** Changes when the draft was replaced from outside: saved, rolled back or discarded. */
  resetKey: number;
  nextVersion: number;
  saving: boolean;
  problems: string[];
  onSave: () => void;
  onDiscard: () => void;
  onVersions: () => void;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState<SectionId | null>(null);
  const [showJson, setShowJson] = useState(false);
  const { draft } = props;

  const changed = SECTIONS.filter((section) => section.keys.some((key) => !same(draft[key], saved[key])));
  const dirty = !same(draft, saved);

  return (
    <aside className="settings" aria-label="ตั้งค่า agent">
      <div className="settings-head">
        <span className="settings-title">
          ตั้งค่า
          {dirty && <span className="badge warn">แก้ {Math.max(changed.length, 1)} จุด</span>}
        </span>
        <div className="row">
          {dirty && (
            <button type="button" className="ghost" onClick={onDiscard}>
              ยกเลิก
            </button>
          )}
          <button type="button" className="primary" disabled={!dirty || saving} onClick={onSave}>
            บันทึก v{nextVersion}
          </button>
        </div>
      </div>
      <Problems items={problems} />

      <div className="settings-cards">
        {SECTIONS.map(({ id, title, summary, Body }) => {
          const expanded = open === id;
          const isChanged = changed.some((section) => section.id === id);
          return (
            <section key={id} className={isChanged ? 'setting changed' : 'setting'}>
              <button
                type="button"
                className="setting-head"
                aria-expanded={expanded}
                onClick={() => setOpen(expanded ? null : id)}
              >
                <span className="setting-title">
                  {title}
                  {isChanged && <span className="dot" title="ยังไม่บันทึก" />}
                </span>
                <span className="chevron" aria-hidden="true">
                  {expanded ? '−' : '+'}
                </span>
                {!expanded && <span className="setting-summary">{summary(props)}</span>}
              </button>
              {expanded && (
                <div className="setting-body">
                  <Body {...props} />
                </div>
              )}
            </section>
          );
        })}
      </div>

      <div className="settings-foot">
        <button type="button" className="ghost" onClick={onVersions}>
          เวอร์ชัน
        </button>
        <button type="button" className="ghost" onClick={() => setShowJson(true)}>
          JSON
        </button>
        <button type="button" className="ghost danger" onClick={onDelete}>
          ลบ agent
        </button>
      </div>

      {showJson && (
        <Modal title={`JSON ของ ${draft.name}`} wide onClose={() => setShowJson(false)}>
          <JsonTab key={resetKey} {...props} />
        </Modal>
      )}
    </aside>
  );
}
