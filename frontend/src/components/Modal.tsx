import { useEffect, useRef, type ReactNode } from 'react';

// A native <dialog>, so Escape closes it and focus stays inside.
export function Modal({
  title,
  onClose,
  wide,
  children,
}: {
  title: string;
  onClose: () => void;
  wide?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  return (
    <dialog ref={ref} className={wide ? 'modal modal-wide' : 'modal'} onClose={onClose}>
      <div className="modal-head">
        <h2>{title}</h2>
        <button type="button" className="ghost" aria-label="ปิด" onClick={onClose}>
          ✕
        </button>
      </div>
      {children}
    </dialog>
  );
}
