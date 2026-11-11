import { useEffect, useState } from 'react';
import { api, messageOf, type AuditRow, type AuditRun, type AuditStats } from '../api';
import { Problems } from '../components/Problems';

const time = (iso: string) => new Date(iso).toLocaleString('th-TH');

function RunRows({ requestId }: { requestId: string }) {
  const [rows, setRows] = useState<AuditRow[] | null>(null);

  useEffect(() => {
    api.auditRows(requestId).then((r) => setRows(r.rows.slice().reverse()));
  }, [requestId]);

  if (!rows) return <p className="muted">กำลังโหลด…</p>;
  return (
    <table className="table nested">
      <thead>
        <tr>
          <th>เหตุการณ์</th>
          <th>สถานะ</th>
          <th>คำขอ</th>
          <th>ผลลัพธ์</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id}>
            <td>
              {row.optional_info?.event ?? row.action}
              <div className="muted">{row.resource_type}</div>
            </td>
            <td>
              <span className={row.status === 'success' ? 'badge ok' : 'badge warn'}>{row.status}</span>
            </td>
            <td className="wrap-cell">{row.request}</td>
            <td className="wrap-cell">
              <code>{row.result ? JSON.stringify(row.result) : ''}</code>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Audit() {
  const [stats, setStats] = useState<AuditStats | null>(null);
  const [runs, setRuns] = useState<AuditRun[]>([]);
  const [changes, setChanges] = useState<AuditRow[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [check, setCheck] = useState('');
  const [problem, setProblem] = useState('');

  useEffect(() => {
    Promise.all([api.auditStats(), api.auditRuns(), api.auditRecent()])
      .then(([s, r, recent]) => {
        setStats(s);
        setRuns(r.runs);
        setChanges(recent.rows.filter((row) => row.optional_info?.event === 'agent.config.change'));
      })
      .catch((error) => setProblem(messageOf(error)));
  }, []);

  async function proveAppendOnly() {
    try {
      const result = await api.appendOnlyCheck();
      const blocked = result.update.blocked && result.delete.blocked;
      setCheck(blocked ? 'ทั้ง UPDATE และ DELETE ถูกปฏิเสธ' : 'แก้หรือลบแถวได้ ซึ่งไม่ควรเกิดขึ้น');
    } catch (error) {
      setCheck(messageOf(error));
    }
  }

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Audit</h1>
          <p className="muted">ทุก run, tool call, การอนุมัติ และการแก้ config ถูกบันทึกแบบเพิ่มได้อย่างเดียว</p>
        </div>
        <div className="row">
          {check && <span className="muted">{check}</span>}
          <button type="button" onClick={() => void proveAppendOnly()}>
            ลองแก้และลบแถว
          </button>
        </div>
      </div>
      {problem && <Problems items={[problem]} />}

      {stats && (
        <div className="stats">
          <div className="stat">
            <span className="muted">แถวทั้งหมด</span>
            <strong>{stats.total_rows}</strong>
          </div>
          {Object.entries(stats.by_resource_type).map(([type, count]) => (
            <div key={type} className="stat">
              <span className="muted">{type}</span>
              <strong>{count}</strong>
            </div>
          ))}
        </div>
      )}

      <h2>Run ล่าสุด</h2>
      {runs.length === 0 && <p className="muted">ยังไม่มี run ลองแชทกับ agent สักตัวก่อน</p>}
      {runs.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>เวลา</th>
              <th>Agent</th>
              <th>ข้อความ</th>
              <th>Tool</th>
              <th>สถานะ</th>
              <th>ใช้เวลา</th>
            </tr>
          </thead>
          <tbody>
            {runs.flatMap((run) => {
              const expanded = open === run.request_id;
              const row = (
                <tr
                  key={run.request_id}
                  className="clickable"
                  onClick={() => setOpen(expanded ? null : run.request_id)}
                >
                  <td className="muted">{time(run.started_at)}</td>
                  <td>{run.agent_name}</td>
                  <td className="wrap-cell">{run.question}</td>
                  <td>{run.tool_calls}</td>
                  <td>
                    <span className={!run.status ? 'badge' : run.failed_rows ? 'badge warn' : 'badge ok'}>
                      {run.status ?? 'ยังไม่จบ'}
                    </span>
                  </td>
                  <td className="muted">{run.elapsed_ms === null ? '' : `${(run.elapsed_ms / 1000).toFixed(1)} วิ`}</td>
                </tr>
              );
              if (!expanded) return [row];
              return [
                row,
                <tr key={`${run.request_id}-rows`}>
                  <td colSpan={6}>
                    <RunRows requestId={run.request_id} />
                  </td>
                </tr>,
              ];
            })}
          </tbody>
        </table>
      )}

      <h2>การแก้ config</h2>
      {changes.length === 0 && <p className="muted">ยังไม่มีการแก้ config</p>}
      {changes.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>เวลา</th>
              <th>ใคร</th>
              <th>ทำอะไร</th>
              <th>field ที่เปลี่ยน</th>
            </tr>
          </thead>
          <tbody>
            {changes.map((row) => (
              <tr key={row.id}>
                <td className="muted">{time(row.created_at)}</td>
                <td>{row.actor_name}</td>
                <td>
                  {row.action} {row.request}
                </td>
                <td className="wrap-cell">
                  <code>{Array.isArray(row.changed_fields) ? row.changed_fields.join(', ') : ''}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
