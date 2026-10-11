import { ReactNode } from 'react';

export type SortState = { key: string; order: 'asc' | 'desc' };

export type Column<T> = {
  key: string;
  header: string;
  /** tooltip on the header, for columns that need explaining */
  help?: string;
  /** set to make the column sortable; the value sent to the server */
  sortKey?: string;
  render: (row: T) => ReactNode;
  className?: string;
};

type DataTableProps<T> = {
  caption: string;
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string;
  /** name of each row for the select checkbox label, e.g. the channel name */
  rowLabel?: (row: T) => string;
  sort?: SortState;
  onSortChange?: (sort: SortState) => void;
  selected?: Set<string>;
  onSelectedChange?: (selected: Set<string>) => void;
  empty?: ReactNode;
};

/**
 * Compact table with a sticky header, sortable columns and row selection.
 * Sorting is controlled: the parent fetches the sorted page.
 */
const DataTable = <T,>({
  caption,
  rows,
  columns,
  rowKey,
  rowLabel,
  sort,
  onSortChange,
  selected,
  onSelectedChange,
  empty,
}: DataTableProps<T>) => {
  const selectable = selected !== undefined && onSelectedChange !== undefined;
  const pageKeys = rows.map(rowKey);
  const selectedOnPage = pageKeys.filter(key => selected?.has(key)).length;
  const allSelected = rows.length > 0 && selectedOnPage === rows.length;

  const toggleAll = () => {
    if (!selectable) return;
    const next = new Set(selected);
    if (allSelected) {
      pageKeys.forEach(key => next.delete(key));
    } else {
      pageKeys.forEach(key => next.add(key));
    }
    onSelectedChange(next);
  };

  const toggleRow = (key: string) => {
    if (!selectable) return;
    const next = new Set(selected);
    if (next.has(key)) {
      next.delete(key);
    } else {
      next.add(key);
    }
    onSelectedChange(next);
  };

  const headerSort = (column: Column<T>) => {
    if (!column.sortKey || !onSortChange) return;
    const order = sort?.key === column.sortKey && sort.order === 'asc' ? 'desc' : ('asc' as const);
    onSortChange({ key: column.sortKey, order });
  };

  const ariaSort = (column: Column<T>) => {
    if (!column.sortKey) return undefined;
    if (sort?.key !== column.sortKey) return 'none' as const;
    return sort.order === 'asc' ? ('ascending' as const) : ('descending' as const);
  };

  return (
    <div className="ux-table-wrap">
      <table className="ux-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {selectable && (
              <th className="ux-col-select">
                <input
                  type="checkbox"
                  aria-label={
                    allSelected ? 'Clear selection on this page' : 'Select all on this page'
                  }
                  checked={allSelected}
                  ref={element => {
                    if (element) element.indeterminate = selectedOnPage > 0 && !allSelected;
                  }}
                  onChange={toggleAll}
                />
              </th>
            )}
            {columns.map(column => (
              <th
                key={column.key}
                className={column.className}
                aria-sort={ariaSort(column)}
                title={column.help}
                scope="col"
              >
                {column.sortKey && onSortChange ? (
                  <button
                    type="button"
                    className="ux-sort-button"
                    onClick={() => headerSort(column)}
                  >
                    {column.header}
                    <span aria-hidden="true" className="ux-sort-mark">
                      {sort?.key === column.sortKey ? (sort.order === 'asc' ? '▲' : '▼') : '↕'}
                    </span>
                  </button>
                ) : (
                  column.header
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={columns.length + (selectable ? 1 : 0)} className="ux-empty">
                {empty ?? 'Nothing to show.'}
              </td>
            </tr>
          )}
          {rows.map(row => {
            const key = rowKey(row);
            const isSelected = selected?.has(key) ?? false;
            return (
              <tr key={key} className={isSelected ? 'selected' : undefined}>
                {selectable && (
                  <td className="ux-col-select">
                    <input
                      type="checkbox"
                      aria-label={`Select ${rowLabel ? rowLabel(row) : key}`}
                      checked={isSelected}
                      onChange={() => toggleRow(key)}
                    />
                  </td>
                )}
                {columns.map(column => (
                  <td key={column.key} className={column.className}>
                    {column.render(row)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

export default DataTable;
