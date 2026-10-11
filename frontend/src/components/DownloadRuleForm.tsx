import { useState } from 'react';
import updateDownloadRule, {
  DownloadRuleType,
  VideoTypeOption,
} from '../api/actions/updateDownloadRule';
import Button from './Button';

const VIDEO_TYPES: VideoTypeOption[] = ['videos', 'streams', 'shorts'];

const EMPTY_RULE: DownloadRuleType = {
  enabled: true,
  min_duration: null,
  max_duration: null,
  vid_types: [],
  title_include: '',
  title_exclude: '',
  published_after: null,
  action: 'ignore',
};

/** give it key={JSON.stringify(rule)} so a saved rule resets the draft */
type DownloadRuleFormProps = {
  /** undefined edits the default rule */
  channelId?: string;
  rule: DownloadRuleType | null;
  onSaved: () => void;
  /** save somewhere else instead, e.g. to several channels at once */
  saveRule?: (rule: DownloadRuleType | null) => Promise<{ status: number; error?: unknown }>;
  /** label of the save button */
  saveLabel?: string;
};

const toMinutes = (seconds: number | null) => (seconds === null ? '' : String(seconds / 60));

const fromMinutes = (minutes: string) => {
  if (minutes.trim() === '') return null;
  return Math.round(Number(minutes) * 60);
};

const DownloadRuleForm = ({
  channelId,
  rule,
  onSaved,
  saveRule,
  saveLabel = 'Save rule',
}: DownloadRuleFormProps) => {
  const [draft, setDraft] = useState<DownloadRuleType>(rule ?? EMPTY_RULE);
  const [error, setError] = useState<string | null>(null);

  const update = (changes: Partial<DownloadRuleType>) => setDraft({ ...draft, ...changes });

  const toggleType = (vidType: VideoTypeOption) => {
    const selected = draft.vid_types.includes(vidType)
      ? draft.vid_types.filter(type => type !== vidType)
      : [...draft.vid_types, vidType];
    update({ vid_types: selected });
  };

  const save = async (toSave: DownloadRuleType | null) => {
    setError(null);
    const response = saveRule
      ? await saveRule(toSave)
      : await updateDownloadRule(channelId, toSave);
    if (response.status >= 400) {
      setError(JSON.stringify(response.error ?? ('data' in response ? response.data : '')));
      return;
    }
    onSaved();
  };

  return (
    <div className="download-rule-form">
      <div className="settings-box-wrapper">
        <div>
          <p>Rule enabled</p>
        </div>
        <input
          aria-label="Rule enabled"
          type="checkbox"
          checked={draft.enabled}
          onChange={event => update({ enabled: event.target.checked })}
        />
      </div>
      <div className="settings-box-wrapper">
        <div>
          <p>Minimum length (minutes)</p>
        </div>
        <input
          aria-label="Minimum length (minutes)"
          type="number"
          min="0"
          step="0.5"
          value={toMinutes(draft.min_duration)}
          onChange={event => update({ min_duration: fromMinutes(event.target.value) })}
        />
      </div>
      <div className="settings-box-wrapper">
        <div>
          <p>Maximum length (minutes)</p>
        </div>
        <input
          aria-label="Maximum length (minutes)"
          type="number"
          min="0"
          step="0.5"
          value={toMinutes(draft.max_duration)}
          onChange={event => update({ max_duration: fromMinutes(event.target.value) })}
        />
      </div>
      <div className="settings-box-wrapper">
        <div>
          <p>Only these types (none checked: all)</p>
        </div>
        <div>
          {VIDEO_TYPES.map(vidType => (
            <label key={vidType}>
              <input
                type="checkbox"
                checked={draft.vid_types.includes(vidType)}
                onChange={() => toggleType(vidType)}
              />{' '}
              {vidType}{' '}
            </label>
          ))}
        </div>
      </div>
      <div className="settings-box-wrapper">
        <div>
          <p>Title must match (regex)</p>
        </div>
        <input
          aria-label="Title must match (regex)"
          type="text"
          value={draft.title_include}
          placeholder="e.g. coaster|train"
          onChange={event => update({ title_include: event.target.value })}
        />
      </div>
      <div className="settings-box-wrapper">
        <div>
          <p>Title must not match (regex)</p>
        </div>
        <input
          aria-label="Title must not match (regex)"
          type="text"
          value={draft.title_exclude}
          placeholder="e.g. #shorts|livestream"
          onChange={event => update({ title_exclude: event.target.value })}
        />
      </div>
      <div className="settings-box-wrapper">
        <div>
          <p>Uploaded on or after</p>
        </div>
        <input
          aria-label="Uploaded on or after"
          type="date"
          value={draft.published_after ?? ''}
          onChange={event => update({ published_after: event.target.value || null })}
        />
      </div>
      <div className="settings-box-wrapper">
        <div>
          <p>Videos that don't match</p>
        </div>
        <select
          aria-label="Videos that don't match"
          value={draft.action}
          onChange={event => update({ action: event.target.value as 'ignore' | 'skip' })}
        >
          <option value="ignore">Add to the queue as ignored, with the reason</option>
          <option value="skip">Leave out of the queue</option>
        </select>
      </div>
      {error && <p className="danger-zone">{error}</p>}
      <div className="button-box">
        <Button label={saveLabel} onClick={() => save(draft)} />{' '}
        {rule && <Button label="Remove rule" onClick={() => save(null)} />}
      </div>
    </div>
  );
};

export default DownloadRuleForm;
