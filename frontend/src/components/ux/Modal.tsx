import { ReactNode, useEffect, useId, useRef } from 'react';

type ModalProps = {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** wider dialog for tables and previews */
  wide?: boolean;
};

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** a modal dialog: Escape and the backdrop close it, focus stays inside while open */
const Modal = ({ title, onClose, children, wide }: ModalProps) => {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const first = panel.current?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? panel.current)?.focus();
    return () => opener?.focus?.();
  }, []);

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== 'Tab' || !panel.current) return;
    const items = Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div className="ux-modal-backdrop" onMouseDown={onClose}>
      <div
        ref={panel}
        className={`ux-modal ${wide ? 'wide' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onMouseDown={event => event.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <div className="ux-modal-head">
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="ux-icon-button" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="ux-modal-body">{children}</div>
      </div>
    </div>
  );
};

export default Modal;
