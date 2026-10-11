import { ReactNode, useEffect, useId, useState } from 'react';
import { ViewStylesType } from '../../configuration/constants/ViewStyle';

type SearchBoxProps = {
  label: string;
  placeholder?: string;
  value: string;
  onChange: (value: string) => void;
  /** wait this long after the last keystroke before calling onChange */
  delay?: number;
};

/** a search field that filters as you type, debounced */
export const SearchBox = ({ label, placeholder, value, onChange, delay = 250 }: SearchBoxProps) => {
  const id = useId();
  const [text, setText] = useState(value);

  useEffect(() => {
    if (text === value) return;
    const timer = setTimeout(() => onChange(text), delay);
    return () => clearTimeout(timer);
  }, [text, value, delay, onChange]);

  return (
    <div className="ux-search">
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <input
        id={id}
        type="search"
        placeholder={placeholder ?? label}
        value={text}
        onChange={event => setText(event.target.value)}
        onKeyDown={event => {
          if (event.key === 'Escape' && text) {
            event.stopPropagation();
            setText('');
          }
        }}
      />
    </div>
  );
};

export type ChipOption<V extends string> = { value: V; label: string; help?: string };

type FilterChipsProps<V extends string> = {
  label: string;
  options: ChipOption<V>[];
  value: V;
  onChange: (value: V) => void;
};

/** one-of-many quick filters shown as chips */
export const FilterChips = <V extends string>({
  label,
  options,
  value,
  onChange,
}: FilterChipsProps<V>) => (
  <div className="ux-chips" role="group" aria-label={label}>
    {options.map(option => (
      <button
        key={option.value}
        type="button"
        className="ux-chip"
        aria-pressed={option.value === value}
        title={option.help}
        onClick={() => onChange(option.value)}
      >
        {option.label}
      </button>
    ))}
  </div>
);

const VIEW_LABELS: Record<ViewStylesType, string> = {
  table: 'Table',
  grid: 'Cards',
  list: 'List',
};

const VIEW_ICONS: Record<ViewStylesType, string> = {
  table: '/img/icon-tableview.svg',
  grid: '/img/icon-gridview.svg',
  list: '/img/icon-listview.svg',
};

type ViewToggleProps = {
  views: ViewStylesType[];
  value: ViewStylesType;
  onChange: (view: ViewStylesType) => void;
};

/** switch between table, cards and list; labelled, not icon-only */
export const ViewToggle = ({ views, value, onChange }: ViewToggleProps) => (
  <div className="ux-view-toggle" role="group" aria-label="View">
    {views.map(view => (
      <button
        key={view}
        type="button"
        aria-pressed={view === value}
        title={`Show as ${VIEW_LABELS[view].toLowerCase()}`}
        onClick={() => onChange(view)}
      >
        <img src={VIEW_ICONS[view]} alt="" aria-hidden="true" />
        <span>{VIEW_LABELS[view]}</span>
      </button>
    ))}
  </div>
);

type ToolbarButtonProps = {
  label: string;
  help: string;
  onClick: () => void;
  icon?: string;
  busy?: boolean;
  disabled?: boolean;
  primary?: boolean;
};

/** a labelled action with a tooltip; the help text is also its accessible description */
export const ToolbarButton = ({
  label,
  help,
  onClick,
  icon,
  busy,
  disabled,
  primary,
}: ToolbarButtonProps) => {
  const helpId = useId();
  return (
    <button
      type="button"
      className={`ux-toolbar-button ${primary ? 'primary' : ''}`}
      title={help}
      aria-describedby={helpId}
      aria-busy={busy || undefined}
      disabled={disabled}
      onClick={onClick}
    >
      {icon && <img src={icon} alt="" aria-hidden="true" className={busy ? 'rotate-img' : ''} />}
      <span>{label}</span>
      <span id={helpId} className="sr-only">
        {help}
      </span>
    </button>
  );
};

type BulkBarProps = {
  count: number;
  noun: string;
  onClear: () => void;
  children: ReactNode;
};

/** actions for the selected rows; hidden while nothing is selected */
export const BulkBar = ({ count, noun, onClear, children }: BulkBarProps) => {
  if (count === 0) return null;
  return (
    <div className="ux-bulk-bar" role="region" aria-label="Actions on selected rows">
      <strong>
        {count} {noun}
        {count === 1 ? '' : 's'} selected
      </strong>
      <div className="ux-bulk-actions">{children}</div>
      <button type="button" className="ux-link-button" onClick={onClear}>
        Clear selection
      </button>
    </div>
  );
};

export type TabOption<V extends string> = { value: V; label: string; count?: number };

type TabsProps<V extends string> = {
  label: string;
  tabs: TabOption<V>[];
  value: V;
  onChange: (value: V) => void;
};

/** tabs with counts; arrow keys move between them */
export const Tabs = <V extends string>({ label, tabs, value, onChange }: TabsProps<V>) => {
  const move = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next = tabs[(index + step + tabs.length) % tabs.length];
    onChange(next.value);
    const buttons = event.currentTarget.parentElement?.querySelectorAll('button');
    buttons?.[(index + step + tabs.length) % tabs.length]?.focus();
  };
  return (
    <div className="ux-tabs" role="tablist" aria-label={label}>
      {tabs.map((tab, index) => (
        <button
          key={tab.value}
          type="button"
          role="tab"
          aria-selected={tab.value === value}
          tabIndex={tab.value === value ? 0 : -1}
          onClick={() => onChange(tab.value)}
          onKeyDown={event => move(event, index)}
        >
          {tab.label}
          {tab.count !== undefined && (
            <span className="ux-tab-count">{tab.count.toLocaleString()}</span>
          )}
        </button>
      ))}
    </div>
  );
};
