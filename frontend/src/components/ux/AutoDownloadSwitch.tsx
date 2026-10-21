import { useId, useState } from 'react';
import updateAutoDownload from '../../api/actions/updateAutoDownload';

export const AUTO_DOWNLOAD_HELP =
  'On: when a check finds new videos, the ones the download rule allows start downloading. Off: they wait in Found videos for you.';

type AutoDownloadSwitchProps = {
  kind: 'channel' | 'playlist';
  id: string;
  name: string;
  value: boolean | undefined;
  /** without edit rights the switch is plain text */
  canEdit?: boolean;
  onSaved: () => void;
};

/** opt a channel or playlist in to auto-download; off unless switched on */
const AutoDownloadSwitch = ({
  kind,
  id,
  name,
  value,
  canEdit,
  onSaved,
}: AutoDownloadSwitchProps) => {
  const helpId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const on = Boolean(value);

  if (!canEdit) {
    return (
      <span className={on ? undefined : 'ux-muted'} title={AUTO_DOWNLOAD_HELP}>
        {on ? 'On' : 'Off'}
      </span>
    );
  }

  const change = async (next: boolean) => {
    setBusy(true);
    setError('');
    let saved = false;
    try {
      const { status } = await updateAutoDownload(kind, id, next);
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
    <span className="ux-auto-download">
      <label className="ux-switch" title={AUTO_DOWNLOAD_HELP}>
        <input
          type="checkbox"
          role="switch"
          aria-label={`Auto-download for ${name}`}
          aria-describedby={helpId}
          checked={on}
          disabled={busy}
          aria-busy={busy || undefined}
          onChange={event => change(event.target.checked)}
        />
        <span>{on ? 'On' : 'Off'}</span>
      </label>
      <span id={helpId} className="sr-only">
        {AUTO_DOWNLOAD_HELP}
      </span>
      {error && (
        <span role="alert" className="ux-error">
          {error}
        </span>
      )}
    </span>
  );
};

export default AutoDownloadSwitch;
