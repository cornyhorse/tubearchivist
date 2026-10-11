import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import DataTable, { Column } from './DataTable';

type Row = { id: string; name: string; size: number };

const rows: Row[] = [
  { id: 'a', name: 'Alpha', size: 3 },
  { id: 'b', name: 'Beta', size: 1 },
];

const columns: Column<Row>[] = [
  { key: 'name', header: 'Name', sortKey: 'name', render: row => row.name },
  { key: 'size', header: 'Size', render: row => row.size, help: 'how big' },
];

afterEach(cleanup);

describe('DataTable', () => {
  it('renders rows and marks sort state', () => {
    render(
      <DataTable
        caption="Things"
        rows={rows}
        columns={columns}
        rowKey={row => row.id}
        sort={{ key: 'name', order: 'asc' }}
        onSortChange={() => {}}
      />,
    );
    expect(screen.getByRole('table', { name: 'Things' })).toBeTruthy();
    const header = screen.getByRole('columnheader', { name: /Name/ });
    expect(header.getAttribute('aria-sort')).toBe('ascending');
    expect(screen.getByRole('columnheader', { name: 'Size' }).getAttribute('title')).toBe(
      'how big',
    );
    expect(screen.getAllByRole('row')).toHaveLength(3);
  });

  it('flips the sort order on the same column', () => {
    const onSortChange = vi.fn();
    const { rerender } = render(
      <DataTable
        caption="Things"
        rows={rows}
        columns={columns}
        rowKey={row => row.id}
        sort={{ key: 'name', order: 'asc' }}
        onSortChange={onSortChange}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Name/ }));
    expect(onSortChange).toHaveBeenLastCalledWith({ key: 'name', order: 'desc' });

    rerender(
      <DataTable
        caption="Things"
        rows={rows}
        columns={columns}
        rowKey={row => row.id}
        sort={{ key: 'other', order: 'desc' }}
        onSortChange={onSortChange}
      />,
    );
    expect(screen.getByRole('columnheader', { name: /Name/ }).getAttribute('aria-sort')).toBe(
      'none',
    );
    fireEvent.click(screen.getByRole('button', { name: /Name/ }));
    expect(onSortChange).toHaveBeenLastCalledWith({ key: 'name', order: 'asc' });
  });

  it('selects rows one by one and all at once', () => {
    const onSelectedChange = vi.fn();
    const { rerender } = render(
      <DataTable
        caption="Things"
        rows={rows}
        columns={columns}
        rowKey={row => row.id}
        rowLabel={row => row.name}
        selected={new Set()}
        onSelectedChange={onSelectedChange}
      />,
    );
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select Beta' }));
    expect([...onSelectedChange.mock.lastCall![0]]).toEqual(['b']);

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all on this page' }));
    expect([...onSelectedChange.mock.lastCall![0]].sort()).toEqual(['a', 'b']);

    rerender(
      <DataTable
        caption="Things"
        rows={rows}
        columns={columns}
        rowKey={row => row.id}
        rowLabel={row => row.name}
        selected={new Set(['a', 'b'])}
        onSelectedChange={onSelectedChange}
      />,
    );
    fireEvent.click(screen.getByRole('checkbox', { name: 'Clear selection on this page' }));
    expect([...onSelectedChange.mock.lastCall![0]]).toEqual([]);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select Alpha' }));
    expect([...onSelectedChange.mock.lastCall![0]]).toEqual(['b']);
  });

  it('shows the empty message across all columns', () => {
    render(
      <DataTable
        caption="Things"
        rows={[]}
        columns={columns}
        rowKey={row => row.id}
        selected={new Set()}
        onSelectedChange={() => {}}
        empty="Nothing here"
      />,
    );
    const cell = within(screen.getAllByRole('row')[1]).getByRole('cell');
    expect(cell.textContent).toBe('Nothing here');
    expect(cell.getAttribute('colspan')).toBe('3');
  });
});
