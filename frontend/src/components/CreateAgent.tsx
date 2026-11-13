import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, messageOf } from '../api';
import { useAgents } from '../agents';
import { Modal } from './Modal';
import { Problems } from './Problems';

export function CreateAgent({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const { reload } = useAgents();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [problems, setProblems] = useState<string[]>([]);

  async function create(event: FormEvent) {
    event.preventDefault();
    try {
      // A new agent starts on the first connection, so it can answer straight away.
      const connection = (await api.connections())[0];
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
      await reload();
      onClose();
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
