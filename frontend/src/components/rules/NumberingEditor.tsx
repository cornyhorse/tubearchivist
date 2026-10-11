import { useEffect, useId, useState } from 'react';
import { notBuilt, previewScheme, SchemePreview, setScheme } from '../../api/ux/client';
import updateEmbyNumbering, {
  EMBY_NUMBERING_LABELS,
  EmbyNumberingType,
} from '../../api/actions/updateEmbyNumbering';

type NumberingEditorProps = {
  kind: 'channel' | 'playlist';
  id: string;
  value: EmbyNumberingType;
  options: EmbyNumberingType[];
  onSaved: () => void;
};

/** pick a scheme, see the resulting Emby names and how many episodes move, then save */
const NumberingEditor = ({ kind, id, value, options, onSaved }: NumberingEditorProps) => {
  const fieldId = useId();
  const [scheme, setSchemeDraft] = useState<EmbyNumberingType>(value);
  const [preview, setPreview] = useState<SchemePreview | null>();
  const [status, setStatus] = useState('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await previewScheme(kind, id, scheme);
        if (!cancelled) setPreview(notBuilt(response) ? null : (response.data ?? null));
      } catch {
        if (!cancelled) setPreview(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [kind, id, scheme]);

  const save = async () => {
    setStatus('Saving…');
    const response = await setScheme(kind, id, scheme);
    if (notBuilt(response)) await updateEmbyNumbering(kind, id, scheme);
    setStatus('Saved');
    onSaved();
  };

  const changed = scheme !== value;

  return (
    <div className="ux-numbering-editor">
      <label htmlFor={fieldId}>Number episodes</label>
      <select
        id={fieldId}
        aria-label="Emby episode numbering"
        value={scheme}
        onChange={event => setSchemeDraft(event.target.value as EmbyNumberingType)}
      >
        {options.map(option => (
          <option key={option} value={option}>
            {EMBY_NUMBERING_LABELS[option]}
          </option>
        ))}
      </select>

      {preview && (
        <div aria-live="polite">
          {changed && (
            <p className={preview.renumbers ? 'ux-warning' : 'ux-setting-effect'}>
              {preview.renumbers
                ? `This renumbers ${preview.renumbers} of ${preview.episodes} episodes in Emby. Numbers you set by hand stay.`
                : 'No episodes change number.'}
            </p>
          )}
          <ul className="ux-preview-list">
            {preview.sample.slice(0, 6).map(item => (
              <li key={item.youtube_id}>
                {changed && item.old_name !== item.name && (
                  <>
                    <s className="ux-muted">{item.old_name}</s> →{' '}
                  </>
                )}
                <code>{item.name}</code>
                {item.manual && <span className="ux-badge numbering">set by hand</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="button-box">
        <button type="button" disabled={!changed} onClick={save}>
          {preview?.renumbers && changed ? `Renumber ${preview.renumbers} episodes` : 'Save'}
        </button>
        <span className="ux-setting-status" role="status">
          {status}
        </span>
      </div>
    </div>
  );
};

export default NumberingEditor;
