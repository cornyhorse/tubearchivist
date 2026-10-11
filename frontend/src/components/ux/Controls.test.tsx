import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { BulkBar, FilterChips, SearchBox, ToolbarButton, ViewToggle } from './Controls';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('SearchBox', () => {
  it('debounces typing and clears on Escape', () => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    render(<SearchBox label="Search channels" value="" onChange={onChange} delay={200} />);
    const input = screen.getByRole('searchbox', { name: 'Search channels' });

    fireEvent.change(input, { target: { value: 'tra' } });
    fireEvent.change(input, { target: { value: 'train' } });
    expect(onChange).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(200));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith('train');

    fireEvent.keyDown(input, { key: 'Escape' });
    expect((input as HTMLInputElement).value).toBe('');
    fireEvent.keyDown(input, { key: 'Escape' });
  });
});

describe('FilterChips', () => {
  it('presses the current chip and reports clicks', () => {
    const onChange = vi.fn();
    render(
      <FilterChips
        label="Show"
        options={[
          { value: 'all', label: 'All' },
          { value: 'sub', label: 'Subscribed', help: 'only subscribed' },
        ]}
        value="all"
        onChange={onChange}
      />,
    );
    expect(screen.getByRole('button', { name: 'All' }).getAttribute('aria-pressed')).toBe('true');
    const sub = screen.getByRole('button', { name: 'Subscribed' });
    expect(sub.getAttribute('aria-pressed')).toBe('false');
    expect(sub.getAttribute('title')).toBe('only subscribed');
    fireEvent.click(sub);
    expect(onChange).toHaveBeenCalledWith('sub');
  });
});

describe('ViewToggle', () => {
  it('labels each view', () => {
    const onChange = vi.fn();
    render(<ViewToggle views={['table', 'grid']} value="table" onChange={onChange} />);
    expect(screen.getByRole('button', { name: 'Table' }).getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'Cards' }));
    expect(onChange).toHaveBeenCalledWith('grid');
  });
});

describe('ToolbarButton', () => {
  it('has a visible label and a described tooltip', () => {
    const onClick = vi.fn();
    render(
      <ToolbarButton label="Download all allowed" help="Queues downloads" busy onClick={onClick} />,
    );
    const button = screen.getByRole('button', { name: /Download all allowed/ });
    expect(button.getAttribute('title')).toBe('Queues downloads');
    expect(button.getAttribute('aria-busy')).toBe('true');
    const describedBy = button.getAttribute('aria-describedby')!;
    expect(document.getElementById(describedBy)?.textContent).toBe('Queues downloads');
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalled();
  });
});

describe('BulkBar', () => {
  it('hides with nothing selected', () => {
    const { container } = render(
      <BulkBar count={0} noun="channel" onClear={() => {}}>
        <button>Go</button>
      </BulkBar>,
    );
    expect(container.textContent).toBe('');
  });

  it('counts with plurals and clears', () => {
    const onClear = vi.fn();
    const { rerender } = render(
      <BulkBar count={1} noun="channel" onClear={onClear}>
        <button>Go</button>
      </BulkBar>,
    );
    expect(screen.getByText('1 channel selected')).toBeTruthy();
    rerender(
      <BulkBar count={12} noun="channel" onClear={onClear}>
        <button>Go</button>
      </BulkBar>,
    );
    expect(screen.getByText('12 channels selected')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Clear selection' }));
    expect(onClear).toHaveBeenCalled();
  });
});
