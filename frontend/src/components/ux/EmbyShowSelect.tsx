import { useId, useState } from 'react';
import updateEmbyShow, { EmbyShowType } from '../../api/actions/updateEmbyShow';

export const EMBY_SHOW_HELP =
  'Whether this channel has its own show in Emby. Otherwise its videos are in the Other videos show and reachable through playlists.';

const EMBY_SHOW_LABELS: Record<EmbyShowType, string> = {
  auto: 'Subscribed only (default)',
  always: 'Always',
  never: 'Never (in Other videos)',
};

type EmbyShowSelectProps = {
  id: string;
  name: string;
  value: EmbyShowType | undefined;
  /** without edit rights the value is plain text */
  canEdit?: boolean;
  onSaved: () => void;
};

/** choose whether a channel is its own Emby show */
const EmbyShowSelect = ({ id, name, value, canEdit, onSaved }: EmbyShowSelectProps) => {
  const helpId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const current = value ?? 'auto';

  if (!canEdit) {
    return (
      <span className={current === 'auto' ? 'ux-muted' : undefined} title={EMBY_SHOW_HELP}>
        {EMBY_SHOW_LABELS[current]}
      </span>
    );
  }

  const change = async (next: EmbyShowType) => {
    setBusy(true);
    setError('');
    let saved = false;
    try {
      const { status } = await updateEmbyShow(id, next);
      saved = status < 400;
    } catch {
      saved = false;
    }
    setBusy(false);
    if (!saved) {
      setError('Could not save. Try again.');
      return;
    }
    onSaved();
  };

  return (
    <span className="ux-emby-show">
      <select
        aria-label={`Emby show for ${name}`}
        aria-describedby={helpId}
        title={EMBY_SHOW_HELP}
        value={current}
        disabled={busy}
        aria-busy={busy || undefined}
        onChange={event => change(event.target.value as EmbyShowType)}
      >
        {(Object.keys(EMBY_SHOW_LABELS) as EmbyShowType[]).map(option => (
          <option key={option} value={option}>
            {EMBY_SHOW_LABELS[option]}
          </option>
        ))}
      </select>
      <span id={helpId} className="sr-only">
        {EMBY_SHOW_HELP}
      </span>
      {error && (
        <span role="alert" className="ux-error">
          {error}
        </span>
      )}
    </span>
  );
};

export default EmbyShowSelect;
