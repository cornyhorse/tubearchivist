import { useState } from 'react';
import updateEmbyNumbering, {
  EMBY_NUMBERING_LABELS,
  EmbyNumberingType,
} from '../api/actions/updateEmbyNumbering';

type EmbyNumberingSelectProps = {
  kind: 'channel' | 'playlist';
  id: string;
  value: EmbyNumberingType;
  options: EmbyNumberingType[];
  onSaved?: () => void;
};

const EmbyNumberingSelect = ({ kind, id, value, options, onSaved }: EmbyNumberingSelectProps) => {
  const [saving, setSaving] = useState(false);

  return (
    <div className="settings-box-wrapper">
      <div>
        <p>Emby episode numbering</p>
      </div>
      <select
        name="emby_numbering"
        value={value}
        disabled={saving}
        onChange={async event => {
          setSaving(true);
          await updateEmbyNumbering(kind, id, event.target.value as EmbyNumberingType);
          setSaving(false);
          onSaved?.();
        }}
      >
        {options.map(option => (
          <option key={option} value={option}>
            {EMBY_NUMBERING_LABELS[option]}
          </option>
        ))}
      </select>
    </div>
  );
};

export default EmbyNumberingSelect;
