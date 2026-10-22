import { useId, useState } from 'react';
import updateEmbyTags from '../../api/actions/updateEmbyTags';

export const EMBY_TAGS_HELP =
  'Emby users limited to tagged shows (like David) only see this when it is tagged for them. No tag hides it from them.';

type EmbyTagsSelectProps = {
  kind: 'channel' | 'playlist';
  id: string;
  name: string;
  /** the tags on the item now */
  value: string[] | undefined;
  /** the tags to offer, one switch each */
  knownTags: string[];
  /** without edit rights the tags are plain text */
  canEdit?: boolean;
  onSaved: () => void;
};

/** choose which tag-restricted Emby users see a channel or playlist */
const EmbyTagsSelect = ({
  kind,
  id,
  name,
  value,
  knownTags,
  canEdit,
  onSaved,
}: EmbyTagsSelectProps) => {
  const helpId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const current = value ?? [];
  const others = current.filter(tag => !knownTags.includes(tag));

  if (!canEdit) {
    return (
      <span className={current.length ? undefined : 'ux-muted'} title={EMBY_TAGS_HELP}>
        {current.length ? current.join(', ') : 'Hidden'}
      </span>
    );
  }

  const change = async (tag: string, on: boolean) => {
    const next = on
      ? [...current.filter(item => item !== tag), tag]
      : current.filter(item => item !== tag);
    setBusy(true);
    setError('');
    let saved = false;
    try {
      const { status } = await updateEmbyTags(kind, id, next);
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
    <span className="ux-emby-tags">
      {knownTags.map(tag => (
        <label key={tag} className="ux-switch" title={EMBY_TAGS_HELP}>
          <input
            type="checkbox"
            role="switch"
            aria-label={`Visible to ${tag}: ${name}`}
            aria-describedby={helpId}
            checked={current.includes(tag)}
            disabled={busy}
            aria-busy={busy || undefined}
            onChange={event => change(tag, event.target.checked)}
          />
          <span>Visible to {tag}</span>
        </label>
      ))}
      {others.map(tag => (
        <span key={tag} className="ux-muted" title="Another tag on this show; kept as it is">
          {tag}
        </span>
      ))}
      {!knownTags.length && !others.length && <span className="ux-muted">Hidden</span>}
      <span id={helpId} className="sr-only">
        {EMBY_TAGS_HELP}
      </span>
      {error && (
        <span role="alert" className="ux-error">
          {error}
        </span>
      )}
    </span>
  );
};

export default EmbyTagsSelect;
