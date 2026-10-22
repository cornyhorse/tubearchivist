import { useId, useState } from 'react';
import updateEmbyPlaylist from '../../api/actions/updateEmbyPlaylist';

export const EMBY_PLAYLIST_HELP =
  "On: this playlist is kept as a playlist in Emby for the users named by its tags and the default users. Turn it off for a channel's uploads playlist.";

type EmbyPlaylistSwitchProps = {
  id: string;
  name: string;
  /** unset counts as on, like the server default */
  value: boolean | undefined;
  /** without edit rights the switch is plain text */
  canEdit?: boolean;
  onSaved: () => void;
};

/** keep an Emby playlist for a TA playlist; on unless switched off */
const EmbyPlaylistSwitch = ({ id, name, value, canEdit, onSaved }: EmbyPlaylistSwitchProps) => {
  const helpId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const on = value !== false;

  if (!canEdit) {
    return (
      <span className={on ? undefined : 'ux-muted'} title={EMBY_PLAYLIST_HELP}>
        {on ? 'On' : 'Off'}
      </span>
    );
  }

  const change = async (next: boolean) => {
    setBusy(true);
    setError('');
    let saved = false;
    try {
      const { status } = await updateEmbyPlaylist(id, next);
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
      <label className="ux-switch" title={EMBY_PLAYLIST_HELP}>
        <input
          type="checkbox"
          role="switch"
          aria-label={`Emby playlist for ${name}`}
          aria-describedby={helpId}
          checked={on}
          disabled={busy}
          aria-busy={busy || undefined}
          onChange={event => change(event.target.checked)}
        />
        <span>{on ? 'On' : 'Off'}</span>
      </label>
      <span id={helpId} className="sr-only">
        {EMBY_PLAYLIST_HELP}
      </span>
      {error && (
        <span role="alert" className="ux-error">
          {error}
        </span>
      )}
    </span>
  );
};

export default EmbyPlaylistSwitch;
