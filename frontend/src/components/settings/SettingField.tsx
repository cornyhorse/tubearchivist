import { ReactNode, useId, useState } from 'react';
import { AppSettingsConfigType } from '../../api/loader/loadAppsettingsConfig';
import {
  getSetting,
  isDefault,
  SETTING_DEFAULTS,
  SettingDef,
  SettingPath,
  SettingValue,
} from '../../configuration/settingsFields';

type SettingRowProps = {
  label: string;
  help: ReactNode;
  htmlFor?: string;
  isDefault?: boolean;
  effect?: string;
  danger?: boolean;
  status?: ReactNode;
  /** id for the help line, so the control can point at it with aria-describedby */
  helpId?: string;
  children: ReactNode;
};

/** label, one line of help, the control, and what it does now */
export const SettingRow = ({
  label,
  help,
  htmlFor,
  isDefault: atDefault,
  effect,
  danger,
  status,
  helpId: givenHelpId,
  children,
}: SettingRowProps) => {
  const ownHelpId = useId();
  const helpId = givenHelpId ?? ownHelpId;
  return (
    <div className={`ux-setting ${danger ? 'danger' : ''}`}>
      <div className="ux-setting-text">
        <label htmlFor={htmlFor} className="ux-setting-label">
          {label}
          {atDefault && (
            <span className="ux-default-badge" title="This is TA's default value">
              default
            </span>
          )}
        </label>
        <p className="ux-setting-help" id={helpId}>
          {help}
        </p>
      </div>
      <div className="ux-setting-control">
        {children}
        {effect && <p className="ux-setting-effect">{effect}</p>}
        <p className="ux-setting-status" aria-live="polite">
          {status}
        </p>
      </div>
    </div>
  );
};

type SettingFieldProps = {
  def: SettingDef;
  config: AppSettingsConfigType;
  onSave: (path: SettingPath, value: SettingValue) => Promise<void>;
};

const toText = (value: SettingValue) => (value === null ? '' : String(value));

/** one application setting: text and numbers save on Save, toggles and selects at once */
const SettingField = ({ def, config, onSave }: SettingFieldProps) => {
  const id = useId();
  const helpId = useId();
  const saved = getSetting(config, def.path);
  const [draft, setDraft] = useState(toText(saved));
  const [status, setStatus] = useState<string>('');

  if (def.visible && !def.visible(config)) return null;

  const save = async (value: SettingValue) => {
    setStatus('Saving…');
    try {
      await onSave(def.path, value);
      setDraft(toText(value));
      setStatus('Saved');
      setTimeout(() => setStatus(''), 2500);
    } catch (error) {
      const message = (error as { message?: string })?.message;
      setStatus(`Not saved: ${message ?? 'something went wrong'}`);
    }
  };

  const parsed = (): SettingValue => {
    if (draft.trim() === '') return null;
    return def.kind === 'number' ? Number(draft) : draft.trim();
  };

  const dirty = (def.kind === 'number' || def.kind === 'text') && draft !== toText(saved);
  const atDefault = isDefault(def.path, saved);
  const fallback = SETTING_DEFAULTS[def.path];

  let control: ReactNode;
  if (def.kind === 'toggle') {
    control = (
      <label className="ux-switch">
        <input
          id={id}
          aria-describedby={helpId}
          type="checkbox"
          role="switch"
          checked={Boolean(saved)}
          onChange={event => save(event.target.checked)}
        />
        <span>{saved ? 'On' : 'Off'}</span>
      </label>
    );
  } else if (def.kind === 'select') {
    control = (
      <select
        id={id}
        aria-describedby={helpId}
        value={toText(saved) || def.options?.[0]?.value}
        onChange={event => save(event.target.value)}
      >
        {def.options?.map(option => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    );
  } else {
    control = (
      <form
        className="ux-inline-form"
        onSubmit={event => {
          event.preventDefault();
          if (dirty) save(parsed());
        }}
      >
        <input
          id={id}
          aria-describedby={helpId}
          type={def.kind === 'number' ? 'number' : 'text'}
          min={def.kind === 'number' ? 0 : undefined}
          value={draft}
          placeholder={def.placeholder}
          onChange={event => setDraft(event.target.value)}
        />
        {def.unit && <span className="ux-unit">{def.unit}</span>}
        {dirty && (
          <>
            <button type="submit">Save</button>
            <button
              type="button"
              className="ux-link-button"
              onClick={() => setDraft(toText(saved))}
            >
              Cancel
            </button>
          </>
        )}
        {!dirty && !atDefault && (
          <button
            type="button"
            className="ux-link-button"
            title={`Go back to ${fallback === null ? 'empty' : fallback}`}
            onClick={() => save(fallback)}
          >
            Reset to default
          </button>
        )}
      </form>
    );
  }

  return (
    <SettingRow
      label={def.label}
      help={def.help}
      htmlFor={id}
      isDefault={atDefault}
      effect={def.effect?.(saved)}
      danger={def.danger}
      status={status}
      helpId={helpId}
    >
      {control}
    </SettingRow>
  );
};

export default SettingField;
