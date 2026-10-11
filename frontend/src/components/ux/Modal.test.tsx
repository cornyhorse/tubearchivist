import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Modal from './Modal';

afterEach(cleanup);

describe('Modal', () => {
  it('is a labelled dialog that focuses its first control', () => {
    render(
      <Modal title="Add videos" onClose={() => {}}>
        <input aria-label="Links" />
      </Modal>,
    );
    const dialog = screen.getByRole('dialog', { name: 'Add videos' });
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Close' }));
  });

  it('closes on Escape, the close button and the backdrop, not inside clicks', () => {
    const onClose = vi.fn();
    const { container } = render(
      <Modal title="Edit" onClose={onClose}>
        <p>body</p>
      </Modal>,
    );
    fireEvent.mouseDown(screen.getByText('body'));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    fireEvent.mouseDown(container.querySelector('.ux-modal-backdrop')!);
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it('keeps Tab inside the dialog', () => {
    render(
      <Modal title="Edit" onClose={() => {}} wide>
        <input aria-label="Last" />
      </Modal>,
    );
    const dialog = screen.getByRole('dialog');
    const first = screen.getByRole('button', { name: 'Close' });
    const last = screen.getByRole('textbox', { name: 'Last' });

    last.focus();
    fireEvent.keyDown(dialog, { key: 'Tab' });
    expect(document.activeElement).toBe(first);

    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(last);

    fireEvent.keyDown(dialog, { key: 'a' });
    expect(dialog.className).toContain('wide');
  });

  it('gives focus back to the opener', () => {
    const opener = document.createElement('button');
    document.body.appendChild(opener);
    opener.focus();
    const { unmount } = render(
      <Modal title="Edit" onClose={() => {}}>
        x
      </Modal>,
    );
    expect(document.activeElement).not.toBe(opener);
    unmount();
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });
});
